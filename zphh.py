import json
import logging
import requests
import uuid
import threading
import time
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from plugins import Plugin, Event, EventAction, EventContext, register
from common.log import logger
from datetime import datetime, timedelta

@register(
    name="ZPHH",
    desc="AI绘画插件",
    version="1.0",
    author="lanvent",
)
class ZPHHPlugin(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self.conversation_id = ""
        self.config = self._load_config()
        # 初始化时刷新access_token
        if not self.refresh_access_token():
            logger.error("[ZPHH] Failed to refresh access token on initialization")
        # 启动定时刷新token的线程
        self._start_token_refresh_timer()
        logger.info("[ZPHH] plugin initialized")

    def _start_token_refresh_timer(self):
        """启动定时刷新token的线程"""
        def refresh_timer():
            while True:
                # 每隔1小时刷新一次token
                time.sleep(3600)
                logger.info("[ZPHH] Refreshing access token...")
                self.refresh_access_token()

        # 启动后台线程
        thread = threading.Thread(target=refresh_timer, daemon=True)
        thread.start()

    def _load_config(self):
        """加载配置文件"""
        try:
            import os
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            with open(config_path, "r", encoding='utf-8') as f:
                config = json.load(f)
                if 'access_token' not in config:
                    config['access_token'] = ""
                return config
        except Exception as e:
            logger.error(f"[ZPHH] Failed to load config: {e}")
            return {"access_token": ""}

    def get_headers(self):
        """获取请求头"""
        device_id = str(uuid.uuid4()).replace("-", "")
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "App-Name": "chatglm",
            "Authorization": f"Bearer {self.config.get('access_token')}",
            "Connection": "keep-alive",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://chatglm.cn",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "X-App-Platform": "pc",
            "X-App-Version": "0.0.1",
            "X-Device-Brand": "",
            "X-Device-Id": device_id,
            "X-Device-Model": "",
            "X-Exp-Groups": "na_android_config:exp:NA,mainchat_funcall:exp:A,chat_aisearch:exp:A,mainchat_rag:exp:A,mainchat_searchengine:exp:bing,na_4o_config:exp:4o_A,chat_live_4o:exp:A,na_glm4plus_config:exp:open,mainchat_server:exp:A,mainchat_browser:exp:new,mainchat_server_app:exp:A,mobile_history_daycheck:exp:a,mainchat_sug:exp:A",
            "X-Request-Id": str(uuid.uuid4()).replace("-", ""),
            "sec-ch-ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"'
        }

    def get_help_text(self, **kwargs):
        help_text = "AI绘画插件\n"
        help_text += "使用方法:\n"
        commands = self.config.get('commands', {})
        draw_command = commands.get('draw', '绘') if isinstance(commands, dict) else '绘'
        help_text += f"{draw_command} [提示词]: 生成图片\n"
        return help_text

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type != ContextType.TEXT:
            return

        content = e_context["context"].content
        commands = self.config.get('commands', {})
        reset_command = commands.get('reset', 'z重置会话') if isinstance(commands, dict) else 'z重置会话'
        if content == reset_command:
            self.conversation_id = ""
            e_context["reply"] = Reply(ReplyType.INFO, "会话已重置")
            e_context.action = EventAction.BREAK_PASS
            return

        draw_command = commands.get('draw', '绘') if isinstance(commands, dict) else '绘'
        if not content.startswith(draw_command):
            return

        try:
            # 提取用户输入的提示词
            prompt = content[len(draw_command):].strip()
            if not prompt:
                e_context["reply"] = Reply(ReplyType.ERROR, "请在命令后输入绘画提示词")
                e_context.action = EventAction.BREAK_PASS
                return

            # 发送等待消息
            e_context["reply"] = Reply(ReplyType.INFO, "正在生成图片,请稍候...")
            e_context["channel"].send(e_context["reply"], e_context["context"])

            # 构建请求数据
            data = {
                "assistant_id": "65a232c082ff90a2ad2f15e2",  # 固定的绘画助手ID
                "conversation_id": self.conversation_id,
                "meta_data": {
                    "cogview": {
                        "aspect_ratio": "1:1",
                        "style": "none",
                        "scene": "none"
                    },
                    "if_plus_model": False,
                    "is_test": False,
                    "input_question_type": "xxxx",
                    "channel": "",
                    "platform": "pc"
                },
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ]
            }

            # 发送绘图请求
            response = requests.post(
                "https://chatglm.cn/chatglm/backend-api/assistant/stream",
                json=data,
                headers=self.get_headers(),
                stream=True,
                timeout=30
            )
            response.raise_for_status()

            # 处理流式响应
            text_response = ""
            image_url = ""
            last_text = ""
            
            for line in response.iter_lines():
                if not line:
                    continue
                    
                line = line.decode('utf-8')
                if line.startswith("event:"):
                    continue
                    
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:])
                        logger.debug(f"[ZPHH] Received data: {data}")
                        
                        if "parts" in data:
                            for part in data["parts"]:
                                if part.get("content"):
                                    for content in part["content"]:
                                        if content.get("type") == "text":
                                            current_text = content.get("text", "")
                                            if current_text != last_text:
                                                text_response = current_text
                                                last_text = current_text
                                        elif content.get("type") == "image" and content.get("image"):
                                            for img in content["image"]:
                                                if img.get("image_url"):
                                                    image_url = img["image_url"]
                                                    break
                                                    
                        if "conversation_id" in data:
                            self.conversation_id = data["conversation_id"]
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"[ZPHH] JSON decode error: {e}")
                        continue

            # 发送最终回复
            if image_url:
                image_reply = Reply(ReplyType.IMAGE_URL, image_url)
                e_context["reply"] = image_reply
                e_context["channel"].send(e_context["reply"], e_context["context"])

            if text_response:
                text_reply = Reply(ReplyType.TEXT, text_response)
                e_context["reply"] = text_reply
            elif not image_url:
                e_context["reply"] = Reply(ReplyType.ERROR, "图片生成失败")

            e_context.action = EventAction.BREAK_PASS
            
        except Exception as e:
            logger.error(f"[ZPHH] 处理绘图请求失败: {e}")
            e_context["reply"] = Reply(ReplyType.ERROR, "绘图请求处理失败,请稍后重试")
            e_context.action = EventAction.BREAK_PASS

    def refresh_access_token(self):
        """刷新access token"""
        try:
            refresh_token = self.config.get('refresh_token')
            if not refresh_token:
                logger.error("[ZPHH] No refresh token available")
                return False
            
            device_id = str(uuid.uuid4()).replace("-", "")
            request_id = str(uuid.uuid4()).replace("-", "")
            
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "App-Name": "chatglm",
                "Authorization": f"Bearer {refresh_token}",
                "Connection": "keep-alive",
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://chatglm.cn",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                "X-App-Platform": "pc",
                "X-App-Version": "0.0.1",
                "X-Device-Brand": "",
                "X-Device-Id": device_id,
                "X-Device-Model": "",
                "X-Exp-Groups": "na_android_config:exp:NA,mainchat_funcall:exp:A,chat_aisearch:exp:A,mainchat_rag:exp:A,mainchat_searchengine:exp:bing,na_4o_config:exp:4o_A,chat_live_4o:exp:A,na_glm4plus_config:exp:open,mainchat_server:exp:A,mainchat_browser:exp:new,mainchat_server_app:exp:A,mobile_history_daycheck:exp:a,mainchat_sug:exp:A",
                "X-Request-Id": request_id,
                "sec-ch-ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"'
            }
            
            response = requests.post(
                "https://chatglm.cn/chatglm/user-api/user/refresh",
                headers=headers,
                json={}
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == 0:
                new_access_token = data["result"]["access_token"]
                logger.info("[ZPHH] Successfully refreshed access token")
                
                # 只更新内存中的token，不写入配置文件
                self.config["access_token"] = new_access_token
                return True
            
            logger.error(f"[ZPHH] Failed to refresh token, status: {data['status']}")
            return False
            
        except Exception as e:
            logger.error(f"[ZPHH] Failed to refresh token: {e}")
            return False
