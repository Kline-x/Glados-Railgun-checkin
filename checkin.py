import datetime
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from logging_config import init_logger


class CheckinStatus(Enum):
    """签到状态"""

    SUCCESS = 0
    REPEAT = 1
    FAILURE = -2


class ExchangePlan(Enum):
    """兑换计划"""

    PLAN100 = "plan100"
    PLAN200 = "plan200"
    PLAN500 = "plan500"


class APIEndpoint(Enum):
    """API端点"""

    CHECKIN = "/api/user/checkin"
    STATUS = "/api/user/status"
    POINTS = "/api/user/points"
    EXCHANGE = "/api/user/exchange"


class LogEmoji:
    """日志 Emoji 常量"""

    SUCCESS = "✅"
    FAIL = "❌"
    REPEAT = "🔄"
    PENDING = "⏳"
    CHECKIN = "🎫"
    STATUS = "📊"
    POINTS = "💰"
    EXCHANGE = "🎁"
    START = "🚀"
    END = "🏁"
    COOKIE = "🍪"
    DOMAIN = "🌐"
    WARNING = "⚠️ "
    ERROR = "🔴"
    INFO = "ℹ️ "


# 各平台合法 User-Agent（版本号无关，仅平台 token 关键）
PLATFORM_UA = {
    "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "macOS": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "iPhone": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Android": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
}


def diagnose_cookie(cookie: str) -> List[str]:
    """诊断 Cookie 格式问题"""
    warnings = []
    has_gld = "gld:sess=" in cookie
    has_gld_sig = "gld:sess.sig=" in cookie
    has_koa = "koa:sess=" in cookie
    has_koa_sig = "koa:sess.sig=" in cookie

    if has_koa and not has_gld:
        warnings.append(
            "检测到当前 Cookie 仍是旧版 koa:sess 格式。GLaDOS 官方已全面升级为 gld:sess / gld:sess.sig，旧凭据已被服务端吊销，请登录 glados.cloud 重新复制最新 Cookie。"
        )
    elif not has_gld and not has_koa:
        warnings.append(
            "未检测到有效 Session 凭据（缺少 gld:sess 或 koa:sess），请检查是否完整复制了 Cookie。"
        )

    if has_gld and not has_gld_sig:
        warnings.append("Cookie 缺少 gld:sess.sig 签名，可能导致无法通过鉴权。")
    elif has_koa and not has_koa_sig and not has_gld:
        warnings.append("Cookie 缺少 koa:sess.sig 签名，可能导致无法通过鉴权。")

    return warnings


def log_method(func):
    """日志装饰器"""

    def wrapper(self, *args, **kwargs):
        method_name = func.__name__
        try:
            result = func(self, *args, **kwargs)
            return result
        except Exception as e:
            logger.error(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] "
                f"{LogEmoji.ERROR} {method_name} 执行失败: {e}"
            )

            DEFAULT_ERRORS = {
                "checkin": {"status": "签到失败", "points": "0", "message": f"执行失败: {e}"},
                "get_status": ("None 天", -2),
                "get_points": ("None 积分", 0),
                "exchange": "兑换失败",
            }

            if method_name in DEFAULT_ERRORS:
                error_template = DEFAULT_ERRORS[method_name]
                if isinstance(error_template, dict):
                    error_result = error_template.copy()
                    return error_result
                return error_template
            raise

    return wrapper


class Config:
    """应用配置"""

    ENV_PUSH_KEY = "PUSHDEER_SENDKEY"
    ENV_SERVERCHAN_KEY = "SERVERCHAN_SENDKEY"
    ENV_WEBHOOK_URL = "NOTIFY_WEBHOOK_URL"
    ENV_WEBHOOK_TYPE = "NOTIFY_WEBHOOK_TYPE"  # auto|generic|feishu|dingtalk|wecom|discord
    ENV_COOKIES = "GLADOS_COOKIES"
    ENV_EXCHANGE_PLAN = "GLADOS_EXCHANGE_PLAN"
    ENV_VERBOSE = "GLADOS_VERBOSE"
    ENV_DOMAINS = "GLADOS_DOMAINS"
    ENV_USER_AGENT = "GLADOS_USER_AGENT"

    """默认是否输出详细响应"""
    DEFAULT_VERBOSE = False

    """默认检测域名（仅针对 glados.cloud，去除非必要的 railgun.info）"""
    DEFAULT_DOMAINS = ["glados.cloud"]

    """兑换计划列表"""
    EXCHANGE_PLANS = {
        ExchangePlan.PLAN100.value: 100,
        ExchangePlan.PLAN200.value: 200,
        ExchangePlan.PLAN500.value: 500,
    }

    def __init__(self):
        self.push_key: str = ""
        self.serverchan_key: str = ""
        self.webhook_url: str = ""
        self.webhook_type: str = "auto"
        self.cookies_list: List[str] = []
        self.cookie_warnings: List[str] = []
        self.exchange_plan: str = ""
        self.verbose: bool = self.DEFAULT_VERBOSE
        self.domains: List[str] = self.DEFAULT_DOMAINS.copy()
        self.custom_ua: str = ""
        self._load_config()

    def _load_config(self) -> None:
        """加载配置"""
        push_key_env = os.environ.get(self.ENV_PUSH_KEY)
        serverchan_key_env = os.environ.get(self.ENV_SERVERCHAN_KEY)
        webhook_url_env = os.environ.get(self.ENV_WEBHOOK_URL)
        webhook_type_env = os.environ.get(self.ENV_WEBHOOK_TYPE)
        raw_cookies_env = os.environ.get(self.ENV_COOKIES)
        exchange_plan_env = os.environ.get(self.ENV_EXCHANGE_PLAN)
        verbose_env = os.environ.get(self.ENV_VERBOSE)
        domains_env = os.environ.get(self.ENV_DOMAINS)
        custom_ua_env = os.environ.get(self.ENV_USER_AGENT)

        self.push_key = push_key_env.strip() if push_key_env else ""
        self.serverchan_key = serverchan_key_env.strip() if serverchan_key_env else ""
        self.webhook_url = webhook_url_env.strip() if webhook_url_env else ""
        self.custom_ua = custom_ua_env.strip() if custom_ua_env else ""

        allowed_webhook_types = {"auto", "generic", "feishu", "dingtalk", "wecom", "discord"}
        if webhook_type_env:
            w_type = webhook_type_env.strip().lower()
            self.webhook_type = w_type if w_type in allowed_webhook_types else "auto"
        else:
            self.webhook_type = "auto"

        if not raw_cookies_env:
            logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_COOKIES}' 未设置。")
            self.cookies_list = []
        else:
            raw_entries = []
            for line in raw_cookies_env.replace("\r", "\n").split("\n"):
                for item in line.split("&"):
                    c = item.strip()
                    if c:
                        raw_entries.append(c)
            self.cookies_list = raw_entries
            if not self.cookies_list:
                raise ValueError(f"环境变量 '{self.ENV_COOKIES}' 已设置，但未包含任何有效的 Cookie。")

            # 对所有 Cookie 执行前置格式诊断
            for idx, c in enumerate(self.cookies_list, 1):
                warns = diagnose_cookie(c)
                for w in warns:
                    logger.warning(f"{LogEmoji.COOKIE}[{idx}] {LogEmoji.WARNING} {w}")
                    self.cookie_warnings.append(f"Cookie {idx}: {w}")

        # 默认不自动兑换，仅在显式配置有效计划时兑换
        if exchange_plan_env:
            ep = exchange_plan_env.strip().lower()
            if ep in self.EXCHANGE_PLANS:
                self.exchange_plan = ep
                logger.info(f"{LogEmoji.SUCCESS} 使用指定的兑换计划: {self.exchange_plan}")
            else:
                logger.warning(
                    f"{LogEmoji.WARNING} 环境变量 '{self.ENV_EXCHANGE_PLAN}' 的值 '{exchange_plan_env}' 无效，跳过自动兑换。"
                )
                self.exchange_plan = ""
        else:
            self.exchange_plan = ""

        # 支持自定义域名列表
        if domains_env:
            custom_domains = [d.strip() for d in domains_env.split(",") if d.strip()]
            if custom_domains:
                self.domains = custom_domains
                logger.info(f"{LogEmoji.SUCCESS} 使用自定义域名列表: {self.domains}")

        if verbose_env is not None:
            v_lower = verbose_env.lower().strip()
            self.verbose = v_lower in ["true", "1", "yes", "y"]

        logger.info(f"{LogEmoji.INFO} 共加载了 {len(self.cookies_list)} 个 Cookie 用于签到。")
        logger.info(f"{LogEmoji.INFO} 目标域名列表: {', '.join(self.domains)}")
        logger.info(f"{LogEmoji.INFO} 当前 PushDeer 推送: {'已配置' if self.push_key else '未配置'}。")
        logger.info(f"{LogEmoji.INFO} 当前 Server酱 推送: {'已配置' if self.serverchan_key else '未配置'}。")
        logger.info(f"{LogEmoji.INFO} 当前 Webhook 推送: {'已配置 (' + self.webhook_type + ')' if self.webhook_url else '未配置'}。")
        logger.info(f"{LogEmoji.INFO} 自定义 User-Agent: {'已配置' if self.custom_ua else '未配置（使用设备自适应）'}。")
        logger.info(f"{LogEmoji.INFO} 自动兑换计划: {self.exchange_plan or '已关闭（默认）'}。")
        logger.info(f"{LogEmoji.INFO} 详细日志输出: {self.verbose}。")


class API:
    """API 调用"""

    CHECKIN_URL = APIEndpoint.CHECKIN.value
    STATUS_URL = APIEndpoint.STATUS.value
    POINTS_URL = APIEndpoint.POINTS.value
    EXCHANGE_URL = APIEndpoint.EXCHANGE.value

    def __init__(
        self,
        domain: str,
        cookie_index: int = 0,
        verbose: bool = False,
        custom_ua: str = "",
    ):
        self.domain: str = domain
        self.cookie_index: int = cookie_index
        self.verbose: bool = verbose
        self.custom_ua: str = custom_ua
        self.headers: Dict[str, str] = self._get_headers()
        self._device_ua_cache: Dict[str, str] = {}
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # 网络抖动时自动重试（指数退避），针对 429 与常见 5xx 状态码
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def __del__(self):
        """关闭 session"""
        self.close()

    def close(self) -> None:
        """关闭 session"""
        if hasattr(self, "session"):
            try:
                self.session.close()
            except Exception as e:
                logger.error(f"{LogEmoji.ERROR} 关闭 session 时发生错误: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _get_headers(self) -> Dict[str, str]:
        """获取默认请求头"""
        default_ua = self.custom_ua or PLATFORM_UA["Windows"]
        return {
            "origin": f"https://{self.domain}",
            "user-agent": default_ua,
        }

    def _log(self, level: str, emoji: str, message: str, force: bool = False) -> None:
        """统一日志输出方法"""
        prefix = f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}]"
        log_message = f"{prefix} {emoji} {message}"

        if force or self.verbose:
            if level == "info":
                logger.info(log_message)
            elif level == "warning":
                logger.warning(log_message)
            elif level == "error":
                logger.error(log_message)

    def _get_full_url(self, path: str) -> str:
        """获取完整 URL"""
        return f"https://{self.domain}{path}"

    def _make_request(
        self,
        url: str,
        method: str,
        data: Optional[Dict] = None,
        cookies: str = "",
        user_agent: Optional[str] = None,
    ) -> Optional[requests.Response]:
        """发送 HTTP 请求"""
        session_headers = self.headers.copy()
        if user_agent:
            session_headers["user-agent"] = user_agent
        session_headers["cookie"] = cookies

        try:
            if method.upper() == "POST":
                response = self.session.post(url, headers=session_headers, data=data, timeout=(30, 60))
            elif method.upper() == "GET":
                response = self.session.get(url, headers=session_headers, timeout=(30, 60))
            else:
                self._log("error", LogEmoji.ERROR, f"不支持的 HTTP 方法: {method}", force=True)
                return None

            if not response.ok:
                self._log(
                    "warning",
                    LogEmoji.WARNING,
                    f"向 {url} 发起的请求响应状态码 {response.status_code}。响应内容: {response.text[:200]}",
                    force=True,
                )
                return None
            return response
        except requests.exceptions.RequestException as e:
            self._log("error", LogEmoji.ERROR, f"向 {url} 发起请求时发生网络错误: {e}", force=True)
            return None

    def _get_checkin_data(self) -> Dict[str, str]:
        """获取签到数据"""
        return {"token": self.domain}

    @log_method
    def checkin(self, cookies: str) -> Dict[str, Union[str, CheckinStatus]]:
        """执行签到，含自适应设备平台切换重试机制"""
        url = self._get_full_url(self.CHECKIN_URL)
        checkin_data = self._get_checkin_data()

        # 起始 UA：用户自定义 UA > 历史学得设备 UA > 默认 UA
        start_ua = self.custom_ua or self._device_ua_cache.get("_last") or self.headers.get("user-agent")

        raw = self._checkin_attempt(url, checkin_data, cookies, start_ua)

        # 设备不匹配(code 4)时按服务端返回的 loginDevice 切换对应平台的 UA 重试一次
        if raw is not None and raw.get("code") == 4 and raw.get("reason") == "device-mismatch":
            login_device = raw.get("loginDevice")
            recover_ua = self._device_ua_cache.get(login_device) or PLATFORM_UA.get(login_device)
            if recover_ua and recover_ua != start_ua:
                self._log(
                    "warning",
                    LogEmoji.WARNING,
                    f"设备平台不匹配 (loginDevice={login_device})，自动切换 UA 为对应平台重试...",
                    force=True,
                )
                raw = self._checkin_attempt(url, checkin_data, cookies, recover_ua)

        return self._parse_checkin(raw)

    def _checkin_attempt(self, url: str, data: Dict, cookies: str, user_agent: str) -> Optional[Dict]:
        """发起一次签到请求并返回解析后的响应体"""
        response = self._make_request(url, "POST", data, cookies, user_agent=user_agent)
        if not response:
            return None
        try:
            raw = response.json()
        except ValueError:
            self._log("error", LogEmoji.ERROR, "签到响应不是合法的 JSON", force=True)
            return None

        # 缓存学得的设备 UA
        if raw.get("code") == 4 and raw.get("reason") == "device-mismatch":
            login_device = raw.get("loginDevice")
            if login_device in PLATFORM_UA:
                self._device_ua_cache[login_device] = PLATFORM_UA[login_device]
        elif raw.get("code") in (CheckinStatus.SUCCESS.value, CheckinStatus.REPEAT.value):
            self._device_ua_cache["_last"] = user_agent

        return raw

    def _parse_checkin(self, raw: Optional[Dict]) -> Dict[str, Union[str, CheckinStatus]]:
        """解析签到响应并格式化日志"""
        result: Dict[str, Union[str, CheckinStatus]] = {
            "status": "签到失败",
            "points": "0",
            "message": "",
            "code": CheckinStatus.FAILURE,
        }

        if not raw:
            result["message"] = "网络请求失败"
            return result

        code = raw.get("code", -2)
        message = raw.get("message", "无消息字段")
        points = str(raw.get("points", 0))

        if code == CheckinStatus.SUCCESS.value:
            self._log("info", LogEmoji.SUCCESS, f"{{ code: {code}, points: {points}, message: {message} }}", force=True)
            result["code"] = CheckinStatus.SUCCESS
            result["status"] = "签到成功"
            result["points"] = points
            result["message"] = message
        elif code == CheckinStatus.REPEAT.value:
            self._log("info", LogEmoji.REPEAT, f"{{ code: {code}, message: {message} }}", force=True)
            result["code"] = CheckinStatus.REPEAT
            result["status"] = "重复签到"
            result["points"] = "0"
            result["message"] = message
        else:
            reason = raw.get("reason", "")
            if code == 4:
                if reason == "device-mismatch":
                    login_device = raw.get("loginDevice") or "未知"
                    if login_device in PLATFORM_UA:
                        self._log(
                            "error",
                            LogEmoji.ERROR,
                            f"设备平台不匹配 (loginDevice={login_device})，自适应重试后仍失败，建议在 Secret 中添加 GLADOS_USER_AGENT",
                            force=True,
                        )
                    else:
                        self._log(
                            "error",
                            LogEmoji.ERROR,
                            f"设备平台不匹配 (loginDevice={login_device} 不在预置列表)，建议在 Secret 中添加 GLADOS_USER_AGENT",
                            force=True,
                        )
                else:
                    self._log(
                        "error",
                        LogEmoji.ERROR,
                        f"服务端拒绝签到 (code=4, reason={reason})，请检查账户状态或风控",
                        force=True,
                    )
            elif code == -2:
                self._log(
                    "error",
                    LogEmoji.ERROR,
                    f"无权限 (code=-2)：Cookie 可能已失效，请重新登录 {self.domain} 获取包含 gld:sess 的最新 Cookie 更新到 GLADOS_COOKIES",
                    force=True,
                )
            else:
                self._log(
                    "error",
                    LogEmoji.ERROR,
                    f"签到异常 (code={code}, message={message})",
                    force=True,
                )
            result["code"] = CheckinStatus.FAILURE
            result["status"] = "签到失败"
            result["points"] = "0"
            result["message"] = message

        return result

    @log_method
    def get_status(self, cookies: str) -> Tuple[str, int]:
        """获取账号剩余天数状态"""
        url = self._get_full_url(self.STATUS_URL)
        response = self._make_request(url, "GET", cookies=cookies)

        if response:
            try:
                data = response.json()
            except ValueError:
                return "None 天", -2

            code = data.get("code", -2)
            left_days = data.get("data", {}).get("leftDays", None)

            if left_days is not None:
                left_days_int = int(float(left_days))
                self._log("info", LogEmoji.SUCCESS, f"{{ code: {code}, leftDays: {left_days_int} 天 }}")
                return f"{left_days_int} 天", code
            else:
                self._log("info", LogEmoji.FAIL, f"{{ code: {code}, leftDays: {left_days} 天 }}", force=True)
                return "None 天", code
        else:
            self._log("warning", LogEmoji.WARNING, "获取状态失败", force=True)
            return "None 天", -2

    @log_method
    def get_points(self, cookies: str) -> Tuple[str, int]:
        """获取总积分"""
        url = self._get_full_url(self.POINTS_URL)
        response = self._make_request(url, "GET", cookies=cookies)

        if response:
            try:
                data = response.json()
            except ValueError:
                return "None 积分", 0

            code = data.get("code", -2)
            points = data.get("points", None)

            if points is not None:
                points_int = int(float(points))
                self._log("info", LogEmoji.SUCCESS, f"{{ code: {code}, points: {points_int} 积分 }}")
                return f"{points_int} 积分", points_int
            else:
                self._log("info", LogEmoji.FAIL, f"{{ code: {code}, points: {points} 积分 }}", force=True)
                return "None 积分", 0
        else:
            self._log("warning", LogEmoji.WARNING, "获取积分失败", force=True)
            return "None 积分", 0

    @log_method
    def exchange(self, cookies: str, plan: str, required_points: int) -> str:
        """执行积分兑换"""
        url = self._get_full_url(self.EXCHANGE_URL)
        response = self._make_request(url, "POST", {"planType": plan}, cookies)

        if response:
            try:
                data = response.json()
            except ValueError:
                return "兑换失败"

            code = data.get("code", -2)
            message = data.get("message", "未知错误")

            if code == 0:
                self._log("info", LogEmoji.SUCCESS, f"{{ code: {code}, message: {message} }}")
                return f"兑换成功: {plan}"
            else:
                self._log("info", LogEmoji.FAIL, f"{{ code: {code}, message: {message} }}", force=True)
                return f"兑换失败: {message}"
        else:
            self._log("warning", LogEmoji.WARNING, "兑换失败", force=True)
            return "兑换失败"


@dataclass()
class CheckinResult:
    """签到结果实体"""

    cookie_index: int
    domain: str
    status: str = "签到失败"
    points: str = "0"
    days: str = "None"
    points_total: str = "None"
    exchange: str = "未兑换"
    code: CheckinStatus = CheckinStatus.FAILURE

    def to_dict(self) -> Dict[str, Union[str, CheckinStatus]]:
        return asdict(self)


def render_payload(payload: dict, line_sep: str = "\n") -> str:
    """将结构化签到结果渲染为文本"""
    lines = []
    for r in payload.get("rows", []):
        line = (
            f"#{r['index']:<2}  {r['emoji']} {r['status']:<6}  [{r['domain']}]  "
            f"获得 {r['points']} 积分 · 剩余 {r['days']} · 总 {r['points_total']}"
        )
        if r.get("exchange") and r["exchange"] != "未配置自动兑换":
            line += f" · {r['exchange']}"
        lines.append(line)

    note = payload.get("note")
    if note:
        lines.append(note)

    diagnostics = payload.get("diagnostics")
    if diagnostics:
        lines.append("")
        lines.append(diagnostics)

    return line_sep.join(lines)


class PushBackend:
    """推送后端抽象基类"""

    name: str = "base"

    def send(self, title: str, payload: dict) -> bool:
        raise NotImplementedError

    def _render(self, payload: dict) -> str:
        return render_payload(payload, "\n")


class PushDeerBackend(PushBackend):
    """PushDeer 推送后端"""

    name = "PushDeer"

    def __init__(self, push_key: str):
        self.push_key = push_key

    def send(self, title: str, payload: dict) -> bool:
        try:
            from pypushdeer import PushDeer

            PushDeer(pushkey=self.push_key).send_text(title, desp=self._render(payload))
            logger.info(f"{LogEmoji.SUCCESS} [{self.name}] 推送发送成功。")
            return True
        except Exception as e:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: {e}")
            return False


class ServerChanBackend(PushBackend):
    """Server酱 推送后端"""

    name = "Server酱"

    def __init__(self, send_key: str):
        self.send_key = send_key

    def _render(self, payload: dict) -> str:
        return render_payload(payload, "  \n")

    def send(self, title: str, payload: dict) -> bool:
        try:
            from serverchan_sdk import sc_send
        except ImportError:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: 未安装 serverchan_sdk，请先执行 pip install serverchan-sdk。")
            return False

        try:
            resp = sc_send(sendkey=self.send_key, title=title, desp=self._render(payload))
            if isinstance(resp, dict) and resp.get("code") == 0:
                logger.info(f"{LogEmoji.SUCCESS} [{self.name}] 推送发送成功。")
                return True
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: 接口返回 {resp}")
            return False
        except Exception as e:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: {e}")
            return False


class WebhookBackend(PushBackend):
    """通用 Webhook 推送后端（支持飞书 / 钉钉 / 企业微信 / Discord / Generic）"""

    name = "Webhook"

    def __init__(self, webhook_url: str, webhook_type: str = "auto"):
        self.webhook_url = webhook_url
        self.webhook_type = webhook_type

    def _detect_webhook_type(self, url: str) -> str:
        lower = url.lower()
        if "feishu.cn" in lower or "larksuite.com" in lower or "open.feishu" in lower:
            return "feishu"
        if "oapi.dingtalk.com" in lower:
            return "dingtalk"
        if "qyapi.weixin.qq.com" in lower:
            return "wecom"
        if "discord.com/api/webhooks" in lower or "discordapp.com/api/webhooks" in lower:
            return "discord"
        return "generic"

    def _build_payload(self, title: str, content: str, w_type: str) -> Dict[str, object]:
        text = f"{title}\n\n{content}".strip() if content else title
        if w_type == "feishu":
            return {"msg_type": "text", "content": {"text": text}}
        if w_type == "dingtalk":
            return {"msgtype": "text", "text": {"content": text}}
        if w_type == "wecom":
            return {"msgtype": "text", "text": {"content": text}}
        if w_type == "discord":
            return {"content": text[:2000]}
        return {
            "title": title,
            "content": content,
            "text": text,
            "message": text,
            "source": "Glados-Railgun-checkin",
        }

    def send(self, title: str, payload: dict) -> bool:
        content = self._render(payload)
        w_type = self.webhook_type
        if w_type == "auto":
            w_type = self._detect_webhook_type(self.webhook_url)

        body = self._build_payload(title, content, w_type)
        headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "Glados-Railgun-checkin/2.0"}

        try:
            resp = requests.post(self.webhook_url, headers=headers, json=body, timeout=(10, 30))
            if not resp.ok:
                logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: HTTP {resp.status_code}, body={resp.text[:200]}")
                return False
            logger.info(f"{LogEmoji.SUCCESS} [{self.name}] 推送发送成功 (类型={w_type}, HTTP {resp.status_code})。")
            return True
        except Exception as e:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送异常: {e}")
            return False


class PushService:
    """推送服务组合聚合器"""

    def __init__(self, config: Optional[Config]):
        self.backends: List[PushBackend] = []
        if config:
            if getattr(config, "push_key", ""):
                self.backends.append(PushDeerBackend(config.push_key))
            if getattr(config, "serverchan_key", ""):
                self.backends.append(ServerChanBackend(config.serverchan_key))
            if getattr(config, "webhook_url", ""):
                self.backends.append(WebhookBackend(config.webhook_url, config.webhook_type))

    def send(self, title: str, payload: dict) -> bool:
        if not self.backends:
            logger.info(f"{LogEmoji.WARNING} 未配置任何推送密钥/URL，跳过推送通知。")
            return False

        backend_names = "、".join(b.name for b in self.backends)
        logger.info(f"{LogEmoji.INFO} 已配置 {len(self.backends)} 个推送渠道（{backend_names}），开始发送...")
        results = [backend.send(title, payload) for backend in self.backends]
        if any(results):
            logger.info(f"{LogEmoji.SUCCESS} 推送完成：{sum(results)}/{len(results)} 个渠道发送成功。")
            return True
        logger.error(f"{LogEmoji.ERROR} 全部推送渠道发送失败。")
        return False


class Checker:
    """签到核心协调器"""

    def __init__(self, config: Config):
        self.config = config
        self.results: List[CheckinResult] = []

    def _log(self, cookie_idx: int, domain: str, emoji: str, message: str, force: bool = False) -> None:
        if self.config.verbose or force:
            logger.info(f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {emoji} {message}")

    def checkin_all(self):
        """执行所有签到任务"""
        cookie_count = len(self.config.cookies_list)
        domain_count = len(self.config.domains)
        total_tasks = cookie_count * domain_count
        task_idx = 0

        logger.info(f"{LogEmoji.INFO} 共 {cookie_count} 个 Cookie, {domain_count} 个域名, 共 {total_tasks} 个任务")

        for cookie_idx, cookie in enumerate(self.config.cookies_list, 1):
            logger.info(f"{LogEmoji.START} ========== 开始处理 Cookie {cookie_idx} ==========")

            for domain in self.config.domains:
                task_idx += 1
                logger.info(
                    f"{LogEmoji.INFO} ----- 任务 {task_idx}/{total_tasks}: "
                    f"{LogEmoji.COOKIE}[{cookie_idx}] on {LogEmoji.DOMAIN}[{domain}] -----"
                )

                result = self._checkin_on_domain(cookie, cookie_idx, domain)
                self.results.append(result)

                result_message = f"结果: {result.status}"
                if result.code == CheckinStatus.SUCCESS:
                    if self.config.verbose:
                        result_message = (
                            f"结果: {result.status}, 获得 {result.points} 积分, "
                            f"剩余 {result.days}, 总 {result.points_total}, {result.exchange}"
                        )
                    self._log(cookie_idx, domain, LogEmoji.SUCCESS, result_message, force=True)
                else:
                    self._log(cookie_idx, domain, LogEmoji.WARNING, result_message, force=True)

    def _checkin_on_domain(self, cookie: str, cookie_idx: int, domain: str) -> CheckinResult:
        result = CheckinResult(cookie_idx, domain)

        with API(
            domain,
            cookie_idx,
            verbose=self.config.verbose,
            custom_ua=self.config.custom_ua,
        ) as api:
            # 1. 查询剩余天数
            self._log(cookie_idx, domain, LogEmoji.STATUS, "查询剩余天数")
            days_str, status_code = api.get_status(cookie)
            result.days = days_str

            # 2. 执行签到
            self._log(cookie_idx, domain, LogEmoji.CHECKIN, "执行签到")
            checkin_result = api.checkin(cookie)
            result.status = checkin_result["status"]
            result.code = checkin_result.get("code", CheckinStatus.FAILURE)
            result.points = str(checkin_result.get("points", 0))

            # 3. 查询总积分
            self._log(cookie_idx, domain, LogEmoji.POINTS, "查询总积分")
            points_str, points_num = api.get_points(cookie)
            result.points_total = points_str

            # 4. 自动兑换
            if self.config.exchange_plan in self.config.EXCHANGE_PLANS:
                required_points = self.config.EXCHANGE_PLANS[self.config.exchange_plan]
                self._log(
                    cookie_idx,
                    domain,
                    LogEmoji.EXCHANGE,
                    f"开始兑换 {self.config.exchange_plan} (需要 {required_points} 积分)",
                )
                result.exchange = api.exchange(cookie, self.config.exchange_plan, required_points)
            else:
                result.exchange = "未配置自动兑换"
                self._log(cookie_idx, domain, LogEmoji.INFO, "未配置自动兑换计划，跳过。")

        return result

    def get_results(self) -> List[Dict[str, str]]:
        return [result.to_dict() for result in self.results]

    def format_results(self) -> dict:
        results = self.get_results()

        success_count = sum(1 for r in results if r["code"] == CheckinStatus.SUCCESS)
        repeat_count = sum(1 for r in results if r["code"] == CheckinStatus.REPEAT)
        fail_count = sum(1 for r in results if r["code"] == CheckinStatus.FAILURE)

        status_emoji_map = {
            CheckinStatus.SUCCESS: LogEmoji.SUCCESS,
            CheckinStatus.REPEAT: LogEmoji.REPEAT,
            CheckinStatus.FAILURE: LogEmoji.ERROR,
        }

        title = f"GLaDOS 签到 · 成功 {success_count} · 失败 {fail_count} · 重复 {repeat_count}"
        if fail_count > 0 and success_count == 0 and repeat_count == 0:
            title = f"GLaDOS 签到异常（全失败，需检查 Cookie/网络） · 失败 {fail_count}"

        rows = []
        for i, res in enumerate(results, 1):
            rows.append(
                {
                    "index": i,
                    "emoji": status_emoji_map.get(res["code"], LogEmoji.ERROR),
                    "status": res["status"],
                    "domain": res["domain"],
                    "points": res["points"],
                    "days": res["days"],
                    "points_total": res["points_total"],
                    "exchange": res["exchange"],
                }
            )

        diagnostics = ""
        diag_lines = []
        if getattr(self.config, "cookie_warnings", None):
            diag_lines.append("⚠️ Cookie 格式检测异常：")
            for w in self.config.cookie_warnings:
                diag_lines.append(f"• {w}")

        if fail_count > 0 and success_count == 0 and repeat_count == 0:
            if not diag_lines:
                diag_lines.append("⚠️ 诊断提示：")
            diag_lines.extend([
                "1. 若日志显示无权限(code=-2)，说明当前 Cookie 已失效。请在浏览器登录 glados.cloud，复制最新的 gld:sess 与 gld:sess.sig 凭证更新 Secret GLADOS_COOKIES；",
                "2. 若日志显示设备不匹配(code=4)，已自动尝试设备切换；您也可在 Secret 中新增 GLADOS_USER_AGENT 填入当前浏览器的 navigator.userAgent；",
                "3. 当前任务仅针对 glados.cloud 执行签到，已去除非必要的 railgun.info。",
            ])

        if diag_lines:
            diagnostics = "\n".join(diag_lines)

        return {
            "title": title,
            "rows": rows,
            "success": success_count,
            "fail": fail_count,
            "repeat": repeat_count,
            "diagnostics": diagnostics,
        }


logger = init_logger()


def main():
    """主程序入口"""
    exit_code = 0
    try:
        logger.info(f"{LogEmoji.START} 步骤 1: 加载配置")
        config = Config()

        if not config.cookies_list:
            logger.error(f"{LogEmoji.ERROR} 未找到有效的 Cookie, 退出程序。")
            payload = {
                "title": "# GLaDOS 签到失败（未配置 Cookie）",
                "rows": [],
                "diagnostics": "未找到有效的 GLADOS_COOKIES，请在仓库 Settings -> Secrets 中添加对应 Secret。",
            }
        else:
            logger.info(f"{LogEmoji.START} 步骤 2: 执行签到任务")
            checker = Checker(config)
            checker.checkin_all()

            logger.info(f"{LogEmoji.START} 步骤 3: 格式化汇总结果")
            payload = checker.format_results()
            logger.info(f"\n{LogEmoji.END} ========== 签到总结 ==========\n{payload['title']}\n{render_payload(payload)}")

    except Exception as e:
        logger.error(f"{LogEmoji.ERROR} 主程序执行发生未预期异常: {e}")
        payload = {"title": "# 脚本执行出错", "rows": [], "note": f"异常详情: {e}"}
        exit_code = 1

    logger.info(f"{LogEmoji.START} 步骤 4: 发送推送通知")
    push_service = PushService(config if "config" in locals() else None)
    push_service.send(payload["title"], payload)
    logger.info(f"{LogEmoji.END} 签到任务流程结束")

    if exit_code != 0:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
