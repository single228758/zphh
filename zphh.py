import json
import logging
import requests
import uuid
import threading
import time
import os
import base64
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from plugins import Plugin, Event, EventAction, EventContext, register
from common.log import logger
from datetime import datetime, timedelta
from urllib.parse import urlparse
import random
from PIL import Image
import io

@register(
    name="ZPHH",
    desc="AI绘画和视频生成插件",
    version="1.0",
    author="lanvent",
)
class ZPHHPlugin(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
       
        self.conversation_id = ""
        self.config = self._load_config()
        # 初始化等待图片状态，确保初始值为None
        self.waiting_for_image = None
        
        # 添加一个时间戳，用于控制waiting_for_image的有效期
        self.waiting_for_image_timestamp = 0
        
        # 创建用户上传专属目录
        self.user_upload_dir = os.path.join(os.path.dirname(__file__), "user_uploads")
        os.makedirs(self.user_upload_dir, exist_ok=True)
        
        # 创建临时目录
        self.temp_dir = os.path.join(os.path.dirname(__file__), "tmp")
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 初始化时刷新access_token
        if not self.refresh_access_token():
            logger.error("[ZPHH] Failed to refresh access token on initialization")
        # 启动定时刷新token的线程
        self._start_token_refresh_timer()
        logger.info("[ZPHH] plugin initialized")

    def _create_temp_dir(self):
        """创建临时目录用于存储图片"""
        try:
            temp_dir = os.path.join(os.path.dirname(__file__), "tmp")
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
                logger.info(f"[ZPHH] Created temp directory: {temp_dir}")
        except Exception as e:
            logger.error(f"[ZPHH] Failed to create temp directory: {e}")

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

    def get_unified_headers(self, content_type=None, additional_headers=None):
        """生成统一的请求头"""
        device_id = str(uuid.uuid4()).replace("-", "")
        request_id = str(uuid.uuid4()).replace("-", "")
        timestamp = int(time.time() * 1000)
        
        # 基础请求头
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "App-Name": "chatglm",
            "Authorization": f"Bearer {self.config.get('access_token')}",
            "Connection": "keep-alive",
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
            "X-Timestamp": str(timestamp),
            "sec-ch-ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"'
        }
        
        # 添加Content-Type
        if content_type:
            headers["Content-Type"] = content_type
        
        # 添加额外请求头
        if additional_headers:
            headers.update(additional_headers)
        
        return headers

    def api_request(self, method, url, data=None, json_data=None, content_type=None, 
                   additional_headers=None, retry_count=2, timeout=30):
        """统一API请求方法"""
        headers = self.get_unified_headers(content_type, additional_headers)
        
        for retry in range(retry_count):
            try:
                if method.upper() == 'GET':
                    response = requests.get(url, headers=headers, params=data, timeout=timeout)
                elif method.upper() == 'POST':
                    if json_data:
                        response = requests.post(url, headers=headers, json=json_data, timeout=timeout)
                    else:
                        response = requests.post(url, headers=headers, data=data, timeout=timeout)
                elif method.upper() == 'PUT':
                    response = requests.put(url, headers=headers, data=data, timeout=timeout)
                else:
                    logger.error(f"[ZPHH] Unsupported HTTP method: {method}")
                    return None
                
                if response.status_code == 401 and retry < retry_count - 1:
                    # 尝试刷新token
                    if self.refresh_access_token():
                        logger.info("[ZPHH] Token refreshed, retrying request")
                        continue
                
                response.raise_for_status()
                return response
                
            except requests.exceptions.RequestException as e:
                if retry < retry_count - 1:
                    logger.warning(f"[ZPHH] Request failed, retrying ({retry+1}/{retry_count}): {e}")
                    time.sleep(1)
                else:
                    logger.error(f"[ZPHH] Request failed after {retry_count} attempts: {e}")
                    return None
        
        return None

    def get_help_text(self, **kwargs):
        help_text = "AI绘画和视频生成插件\n"
        help_text += "使用方法:\n"
        commands = self.config.get('commands', {})
        draw_command = commands.get('draw', '绘') if isinstance(commands, dict) else '绘'
        video_ref_command = commands.get('video_ref', '智谱参考图') if isinstance(commands, dict) else '智谱参考图'
        video_command = commands.get('video', '智谱视频') if isinstance(commands, dict) else '智谱视频'
        help_text += f"{draw_command} [提示词]: 生成图片\n"
        help_text += f"{video_ref_command} [提示词]: 发送图片后生成视频\n"
        help_text += f"{video_command} [提示词]-[视频风格]-[情感氛围]-[运镜方式]-[比例]: 生成视频\n"
        help_text += "视频风格可选: 无/卡通3D/黑白老照片/油画/电影感\n"
        help_text += "情感氛围可选: 无/温馨和谐/生动活泼/紧张刺激/凄凉寂寞\n"
        help_text += "运镜方式可选: 无/水平/垂直/推近/拉远\n"
        help_text += "比例可选: 1:1/16:9/9:16/3:4\n"
        return help_text

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type != ContextType.TEXT and e_context["context"].type != ContextType.IMAGE:
            return

        # 检查等待图片状态是否超时（5分钟）
        if self.waiting_for_image is not None:
            current_time = time.time()
            if current_time - self.waiting_for_image_timestamp > 300:  # 5分钟超时
                logger.info("[ZPHH] 等待图片超时，重置状态")
                self.waiting_for_image = None

        # 处理图片消息
        if e_context["context"].type == ContextType.IMAGE and self.waiting_for_image is not None:
            self._process_received_image(e_context)
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
        video_ref_command = commands.get('video_ref', '智谱参考图') if isinstance(commands, dict) else '智谱参考图'
        video_command = commands.get('video', '智谱视频') if isinstance(commands, dict) else '智谱视频'
        
        # 先检查是否是文生视频命令
        if content.startswith(video_command):
            self._handle_video_command(content, video_command, e_context)
            return
        # 再检查是否是参考图视频命令
        elif content.startswith(video_ref_command):
            self._handle_video_ref_command(content, video_ref_command, e_context)
            return
        # 最后检查是否是绘画命令
        elif content.startswith(draw_command):
            self._handle_draw_command(content, draw_command, e_context)
            return

    def _handle_draw_command(self, content, draw_command, e_context):
        """处理绘画命令"""
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
                headers=self.get_unified_headers(),
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

    def _handle_video_ref_command(self, content, video_ref_command, e_context):
        """处理参考图视频命令"""
        try:
            # 提取用户输入的提示词
            prompt = content[len(video_ref_command):].strip()
            if not prompt:
                e_context["reply"] = Reply(ReplyType.TEXT, "请在命令后输入视频提示词")
                e_context.action = EventAction.BREAK_PASS
                return

            # 发送等待消息，提示用户发送图片
            e_context["reply"] = Reply(ReplyType.TEXT, "请发送一张参考图片")
            
            # 保存提示词和上下文，等待图片
            self.waiting_for_image = {
                "prompt": prompt,
                "context": e_context
            }
            # 设置等待图片的时间戳
            self.waiting_for_image_timestamp = time.time()
            
            e_context.action = EventAction.BREAK_PASS
            
        except Exception as e:
            logger.error(f"[ZPHH] 处理参考图视频请求失败: {e}")
            e_context["reply"] = Reply(ReplyType.TEXT, f"参考图视频请求处理失败: {str(e)}")
            e_context.action = EventAction.BREAK_PASS
    
    def _get_image_data(self, msg, content):
        """精确获取用户发送的图片"""
        try:
            logger.debug(f"[ZPHH] 开始处理图片消息，原始路径: {content}")
            
            # 确保图片已下载
            if hasattr(msg, '_prepare_fn') and not msg._prepared:
                try:
                    logger.info("[ZPHH] 准备下载图片...")
                    msg._prepare_fn()
                    msg._prepared = True
                    # 添加等待时间，确保文件写入完成
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"[ZPHH] 下载图片失败: {e}")
            
            # 1. 优先使用消息中的直接文件路径
            if hasattr(msg, 'content') and os.path.isfile(msg.content):
                # 添加重试机制，确保文件可访问
                for retry in range(3):
                    try:
                        logger.info(f"[ZPHH] 尝试读取文件: {msg.content} (尝试 {retry+1}/3)")
                        with open(msg.content, 'rb') as f:
                            data = f.read()
                        logger.info(f"[ZPHH] 成功读取文件: {msg.content}, 大小: {len(data)} 字节")
                        return msg.content, data
                    except (IOError, PermissionError) as e:
                        logger.warning(f"[ZPHH] 读取文件失败，重试中: {e}")
                        time.sleep(1)  # 等待文件释放
            
            # 2. 处理URL类型
            if isinstance(content, str) and (content.startswith('http://') or content.startswith('https://')):
                logger.info(f"[ZPHH] 下载URL图片: {content}")
                response = requests.get(content, timeout=30)
                if response.status_code == 200:
                    temp_file = os.path.join(self.user_upload_dir, f"url_upload_{uuid.uuid4()}.jpg")
                    with open(temp_file, 'wb') as f:
                        f.write(response.content)
                    return temp_file, response.content

            # 3. 处理需要下载的消息
            if hasattr(msg, '_prepare_fn') and not msg._prepared:
                try:
                    logger.info("[ZPHH] 准备下载消息中的图片")
                    msg._prepare_fn()
                    msg._prepared = True
                    time.sleep(2)  # 等待下载完成
                    
                    if os.path.isfile(msg.content):
                        logger.info(f"[ZPHH] 成功下载图片: {msg.content}")
                        with open(msg.content, 'rb') as f:
                            return msg.content, f.read()
                except Exception as e:
                    logger.error(f"[ZPHH] 下载图片失败: {e}")

            logger.error(f"[ZPHH] 无法获取图片数据，原始路径: {content}")
            return None, None

        except Exception as e:
            logger.error(f"[ZPHH] 获取图片数据失败: {e}")
            return None, None

    def _process_received_image(self, e_context: EventContext):
        """改进的图片处理函数"""
        if self.waiting_for_image is None:
            return
        
        try:
            context = e_context['context']
            msg = context.kwargs.get('msg')
            
            # 清理历史上传文件
            self._clean_user_uploads()
            
           
            image_path = context.content
            
            # 如果文件不存在，尝试下载
            if not os.path.isfile(image_path) and hasattr(msg, '_prepare_fn') and not getattr(msg, '_prepared', False):
                try:
                    logger.info(f"[ZPHH] 图片不存在，尝试下载: {image_path}")
                    msg._prepare_fn()
                    setattr(msg, '_prepared', True)
                    time.sleep(2)  # 等待文件准备完成
                    
                    # 再次检查文件路径
                    if os.path.isfile(image_path):
                        logger.info(f"[ZPHH] 下载后找到图片文件: {image_path}")
                    else:
                        logger.error(f"[ZPHH] 下载后仍未找到图片文件: {image_path}")
                        original_context = self.waiting_for_image["context"]
                        original_context["reply"] = Reply(ReplyType.TEXT, "获取图片失败，请重新发送图片")
                        original_context.action = EventAction.BREAK_PASS
                        return
                except Exception as e:
                    logger.error(f"[ZPHH] 准备图片文件失败: {e}")
                    original_context = self.waiting_for_image["context"]
                    original_context["reply"] = Reply(ReplyType.TEXT, "下载图片失败，请重新发送图片")
                    original_context.action = EventAction.BREAK_PASS
                    return
            
            # 读取图片数据
            try:
                with open(image_path, 'rb') as f:
                    image_data = f.read()
                logger.info(f"[ZPHH] 成功读取图片: {image_path}, 大小: {len(image_data)} 字节")
            except Exception as e:
                logger.error(f"[ZPHH] 读取图片失败: {e}")
                original_context = self.waiting_for_image["context"]
                original_context["reply"] = Reply(ReplyType.TEXT, "读取图片失败，请重新发送图片")
                original_context.action = EventAction.BREAK_PASS
                return
            
            # 处理后续操作...
            prompt = self.waiting_for_image["prompt"]
            original_context = self.waiting_for_image["context"]
            
            # 上传图片到服务器
            source_id, source_url = self._upload_image(image_data)
            if not source_id or not source_url:
                original_context["reply"] = Reply(ReplyType.TEXT, "上传图片失败，请稍后重试")
                original_context.action = EventAction.BREAK_PASS
                return
            
            # 发送视频生成请求 
            logger.info(f"[ZPHH] 开始发送参考图视频生成请求，提示词: {prompt}, 图片ID: {source_id}")
            
            # 发送等待消息
            original_context["reply"] = Reply(ReplyType.TEXT, "正在生成视频，请稍候...")
            original_context["channel"].send(original_context["reply"], original_context["context"])
            
            # 发送视频生成请求
            task_id = self._send_video_gen_request(prompt, source_id)
            if not task_id:
                original_context["reply"] = Reply(ReplyType.TEXT, "创建视频任务失败，请稍后重试")
                original_context.action = EventAction.BREAK_PASS
                return
            
            # 轮询检查视频生成状态
            video_url = self._check_video_status(task_id)
            if not video_url:
                original_context["reply"] = Reply(ReplyType.TEXT, "获取视频结果失败，请稍后重试")
                original_context.action = EventAction.BREAK_PASS
                return
            
            # 发送视频URL
            video_reply = Reply(ReplyType.VIDEO_URL, video_url)
            original_context["channel"].send(video_reply, original_context["context"])
            
            # 发送成功消息
            original_context["reply"] = Reply(ReplyType.TEXT, "视频生成成功！")
            original_context.action = EventAction.BREAK_PASS
            
            # 最后重要的是：处理完后重置状态
            self.waiting_for_image = None
            
        except Exception as e:
            logger.error(f"[ZPHH] 处理图片失败: {e}")
            if self.waiting_for_image and self.waiting_for_image["context"]:
                self.waiting_for_image["context"]["reply"] = Reply(ReplyType.TEXT, f"处理图片失败: {str(e)}")
            self.waiting_for_image = None
        
        e_context.action = EventAction.BREAK_PASS

    def _clean_user_uploads(self):
        """清理用户上传目录中的历史文件"""
        try:
            # 保留最近1小时的文件
            cutoff_time = time.time() - 3600
            
            for f in os.listdir(self.user_upload_dir):
                file_path = os.path.join(self.user_upload_dir, f)
                if os.path.isfile(file_path):
                    # 检查文件修改时间
                    if os.path.getmtime(file_path) < cutoff_time:
                        try:
                            os.unlink(file_path)
                            logger.debug(f"[ZPHH] 已清理过期文件: {file_path}")
                        except Exception as e:
                            logger.error(f"[ZPHH] 清理文件失败: {e}")
        except Exception as e:
            logger.error(f"[ZPHH] 清理上传目录失败: {e}")

    def _upload_image(self, image_data):
        """上传图片到智谱服务器"""
        try:
            if not image_data:
                logger.error("[ZPHH] 图片数据为空")
                return None, None
            
            file_size = len(image_data)
            logger.debug(f"[ZPHH] 准备上传图片，大小: {file_size} 字节")
            
            # 获取图片的实际尺寸
            try:
                img = Image.open(io.BytesIO(image_data))
                width, height = img.size
                logger.info(f"[ZPHH] 获取到图片实际尺寸: {width}x{height}")
            except Exception as e:
                logger.error(f"[ZPHH] 获取图片尺寸失败，使用默认值: {e}")
                width, height = 800, 800
            
            # 生成随机文件名
            file_name = f"n_v{random.getrandbits(128):032x}.jpg"
            
            # 准备额外的头部信息
            additional_headers = {
                'Content-Type': 'multipart/form-data',
                'stepchat-meta-size': str(file_size)
            }
            
            # 准备表单数据
            from requests_toolbelt.multipart.encoder import MultipartEncoder
            multipart_data = MultipartEncoder(
                fields={
                    'file': ('blob', image_data, 'image/jpeg'),
                    'width': str(width),  # 使用实际宽度
                    'height': str(height)  # 使用实际高度
                }
            )
            
            # 更新Content-Type
            additional_headers['Content-Type'] = multipart_data.content_type
            
            # 发送上传请求
            upload_url = 'https://chatglm.cn/chatglm/video-api/v1/static/upload'
            response = self.api_request(
                'POST', 
                upload_url, 
                data=multipart_data,
                additional_headers=additional_headers
            )
            
            if not response:
                return None, None
            
            data = response.json()
            if data["status"] == 0:
                source_id = data["result"]["source_id"]
                source_url = data["result"]["source_url"]
                logger.info(f"[ZPHH] 图片上传成功: {source_id}")
                return source_id, source_url
            
            logger.error(f"[ZPHH] 图片上传失败: {data}")
            return None, None
            
        except Exception as e:
            logger.error(f"[ZPHH] 上传图片失败: {e}")
            return None, None

    def _send_video_gen_request(self, prompt, source_id):
        """发送参考图视频生成请求"""
        try:
            # 构建请求数据 - 参考图视频的请求格式
            json_data = {
                "prompt": prompt,
                "conversation_id": "",
                "source_list": [source_id],
                "base_parameter_extra": {
                    "generation_pattern": 1,
                    "resolution": 0,
                    "fps": 0,
                    "duration": 1,
                    "generation_ai_audio": 0,
                    "generation_ratio_height": 9,
                    "generation_ratio_width": 16,
                    "activity_type": 0,
                    "label_watermark": 0
                }
            }
            
            # 发送请求
            response = self.api_request(
                'POST',
                "https://chatglm.cn/chatglm/video-api/v1/chat",
                json_data=json_data,
                content_type="application/json;charset=UTF-8"
            )
            
            if not response:
                return None
            
            data = response.json()
            if data["status"] == 0:
                chat_id = data["result"]["chat_id"]
                logger.info(f"[ZPHH] 参考图视频任务创建成功: {chat_id}")
                return chat_id
            
            logger.error(f"[ZPHH] 参考图视频任务创建失败: {data}")
            return None
            
        except Exception as e:
            logger.error(f"[ZPHH] 创建参考图视频任务失败: {e}")
            return None

    def _check_video_status(self, task_id, max_retries=180):
        """改进的视频状态检查函数"""
        try:
            for i in range(max_retries):
                response = self.api_request(
                    'GET',
                    f"https://chatglm.cn/chatglm/video-api/v1/chat/status/{task_id}"
                )
                
                if not response:
                    if i % 12 == 0:  # 每分钟记录一次错误
                        logger.error(f"[ZPHH] 检查视频状态失败，将继续重试")
                    time.sleep(5)
                    continue
                
                data = response.json()
                if data["status"] == 0:
                    result = data["result"]
                    status = result.get("status")
                    
                    if status == "finished":
                        video_url = result.get("video_url")
                        if video_url:
                            logger.info(f"[ZPHH] 视频生成成功: {video_url}")
                            # 清理相关临时文件
                            self._clean_video_temp_files(video_url)
                            return video_url
                    elif status == "failed":
                        logger.error(f"[ZPHH] 视频生成失败: {result.get('msg')}")
                        return None
                    
                    # 输出当前状态
                    msg = result.get("msg", "处理中...")
                    if i % 12 == 0:  # 每分钟输出一次日志
                        logger.info(f"[ZPHH] 视频生成状态: {msg}")
                
                time.sleep(5)
            
            logger.error("[ZPHH] 视频生成超时")
            return None
            
        except Exception as e:
            logger.error(f"[ZPHH] 检查视频状态失败: {e}")
            return None

    def _clean_video_temp_files(self, video_url):
        """清理视频相关的临时文件"""
        try:
            # 从URL中提取文件名
            video_name = os.path.basename(urlparse(video_url).path)
            base_name = os.path.splitext(video_name)[0]
            
            # 清理临时目录中的相关文件
            for f in os.listdir(self.temp_dir):
                if f.startswith(base_name):
                    file_path = os.path.join(self.temp_dir, f)
                    try:
                        os.remove(file_path)
                        logger.debug(f"[ZPHH] 已清理临时文件: {file_path}")
                    except Exception as e:
                        logger.error(f"[ZPHH] 清理文件失败: {e}")
        except Exception as e:
            logger.error(f"[ZPHH] 清理视频临时文件失败: {e}")

    def refresh_access_token(self):
        """刷新access token"""
        try:
            refresh_token = self.config.get('refresh_token')
            if not refresh_token:
                logger.error("[ZPHH] No refresh token available")
                return False
            
            json_data = {}
            
            # 使用统一的API请求方法
            response = self.api_request(
                'POST',
                "https://chatglm.cn/chatglm/user-api/user/refresh",
                json_data=json_data,
                content_type="application/json;charset=UTF-8",
                additional_headers={"Authorization": f"Bearer {refresh_token}"}
            )
            
            if not response:
                return False
            
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

    def _handle_video_command(self, content, video_command, e_context):
        """处理文生视频命令"""
        try:
            # 提取命令后的内容
            params = content[len(video_command):].strip()
            
            # 解析参数
            prompt, video_style, emotional_atmosphere, mirror_mode, ratio = self._parse_video_params(params)
            
            # 发送等待消息
            e_context["reply"] = Reply(ReplyType.TEXT, "正在生成视频，请稍候...")
            e_context["channel"].send(e_context["reply"], e_context["context"])
            
            # 发送视频生成请求
            task_id = self._send_text_video_request(prompt, video_style, emotional_atmosphere, mirror_mode, ratio)
            if not task_id:
                e_context["reply"] = Reply(ReplyType.TEXT, "创建视频任务失败，请稍后重试")
                e_context.action = EventAction.BREAK_PASS
                return
            
            # 轮询检查视频生成状态
            video_url = self._check_video_status(task_id)
            if not video_url:
                e_context["reply"] = Reply(ReplyType.TEXT, "获取视频结果失败，请稍后重试")
                e_context.action = EventAction.BREAK_PASS
                return
            
            # 发送视频URL
            video_reply = Reply(ReplyType.VIDEO_URL, video_url)
            e_context["channel"].send(video_reply, e_context["context"])
            
            # 发送成功消息
            params_info = []
            if video_style != "无":
                params_info.append(f"风格:{video_style}")
            if emotional_atmosphere != "无":
                params_info.append(f"氛围:{emotional_atmosphere}")
            if mirror_mode != "无":
                params_info.append(f"运镜:{mirror_mode}")
            params_info.append(f"比例:{ratio[0]}:{ratio[1]}")
            
            params_text = "，".join(params_info)
            e_context["reply"] = Reply(ReplyType.TEXT, f"视频生成成功！\n使用参数：{params_text}")
            e_context.action = EventAction.BREAK_PASS
            
        except Exception as e:
            logger.error(f"[ZPHH] 处理文生视频请求失败: {e}")
            e_context["reply"] = Reply(ReplyType.TEXT, f"处理文生视频请求失败: {str(e)}")
            e_context.action = EventAction.BREAK_PASS

    def _parse_video_params(self, params):
        """解析视频参数"""
        # 默认参数
        video_style = "无"
        emotional_atmosphere = "无"
        mirror_mode = "无"
        ratio = "1:1"
        
        # 分割参数
        parts = params.split('-')
        prompt = parts[0].strip()
        
        # 解析其他参数
        for part in parts[1:]:
            part = part.strip()
            # 检查是否是比例参数
            if ':' in part and part.replace(':', '').isdigit():
                # 验证是否是支持的比例
                supported_ratios = ["1:1", "16:9", "9:16", "3:4"]
                if part in supported_ratios:
                    ratio = part
                else:
                    # 如果不在预定义列表中，仍然接受它，但记录警告
                    logger.warning(f"[ZPHH] 使用了非标准比例: {part}")
                    ratio = part
            # 检查是否是视频风格
            elif part in ["无", "卡通3D", "黑白老照片", "油画", "电影感"]:
                video_style = part
            # 检查是否是情感氛围
            elif part in ["无", "温馨和谐", "生动活泼", "紧张刺激", "凄凉寂寞"]:
                emotional_atmosphere = part
            # 检查是否是运镜方式
            elif part in ["无", "水平", "垂直", "推近", "拉远"]:
                mirror_mode = part
        
        # 解析比例参数
        ratio_parts = ratio.split(':')
        ratio_width = int(ratio_parts[0])
        ratio_height = int(ratio_parts[1])
        
        return prompt, video_style, emotional_atmosphere, mirror_mode, (ratio_width, ratio_height)

    def _send_text_video_request(self, prompt, video_style, emotional_atmosphere, mirror_mode, ratio):
        """发送文生视频请求"""
        try:
            # 构建请求数据 - 文生视频的请求格式
            json_data = {
                "prompt": prompt,
                "conversation_id": "",
                "advanced_parameter_extra": {
                    "video_style": video_style,
                    "emotional_atmosphere": emotional_atmosphere,
                    "mirror_mode": mirror_mode
                },
                "base_parameter_extra": {
                    "generation_pattern": 1,
                    "resolution": 0,
                    "fps": 0,
                    "duration": 1,
                    "generation_ai_audio": 0,
                    "generation_ratio_height": ratio[1],
                    "generation_ratio_width": ratio[0],
                    "activity_type": 0,
                    "label_watermark": 0
                }
            }
            
            # 打印请求数据，方便调试
            logger.debug(f"[ZPHH] 视频生成请求参数: {json.dumps(json_data, ensure_ascii=False)}")
            
            # 发送请求
            response = self.api_request(
                'POST',
                "https://chatglm.cn/chatglm/video-api/v1/chat",
                json_data=json_data,
                content_type="application/json;charset=UTF-8"
            )
            
            if not response:
                return None
            
            data = response.json()
            if data["status"] == 0:
                chat_id = data["result"]["chat_id"]
                logger.info(f"[ZPHH] 文生视频任务创建成功: {chat_id}")
                return chat_id
            
            logger.error(f"[ZPHH] 文生视频任务创建失败: {data}")
            return None
            
        except Exception as e:
            logger.error(f"[ZPHH] 创建文生视频任务失败: {e}")
            return None
