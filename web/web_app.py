"""网盘拉新统一 Web 管理界面。

在原夸克 web_app.py 基础上扩展：所有接口增加 platform 参数，
路由到对应适配器（baidu / quark）。
"""
import os
import json
import threading
from datetime import datetime

from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder="templates", static_folder="static")

CONFIG_FILE = os.path.join("data", "web_config.json")
LOG_FILE = os.path.join("data", "logs", "web_run.log")
PREVIEW_CACHE_FILE = os.path.join("data", "preview_cache.json")

from adapters import ADAPTERS


def ensure_dirs():
    os.makedirs("data", exist_ok=True)
    os.makedirs("data/logs", exist_ok=True)
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)


def log_run(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())


def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_run(f"加载配置失败: {e}", "ERROR")
    return {"kw": "1", "include": [], "exclude": ["可搜索"],
            "count": 5, "platform": "quark"}


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log_run(f"保存配置失败: {e}", "ERROR")
        return False


def save_preview_cache(items, config):
    try:
        with open(PREVIEW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"items": items, "config": config}, f,
                      ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log_run(f"保存预览缓存失败: {e}", "ERROR")
        return False


def load_preview_cache():
    try:
        if os.path.exists(PREVIEW_CACHE_FILE):
            with open(PREVIEW_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_run(f"加载预览缓存失败: {e}", "ERROR")
    return None


def clear_preview_cache():
    try:
        if os.path.exists(PREVIEW_CACHE_FILE):
            os.remove(PREVIEW_CACHE_FILE)
    except Exception:
        pass


def run_adapter(platform, kw, include, exclude, count, preview_only=False,
                selected_items=None):
    """在线程中运行适配器，返回结果 dict。"""
    if platform not in ADAPTERS:
        return {"code": 400, "message": f"未知平台: {platform}"}
    try:
        return ADAPTERS[platform](count=count, kw=kw, include=include,
                                  exclude=exclude, preview_only=preview_only,
                                  selected_items=selected_items)
    except Exception as e:
        log_run(f"任务执行失败: {e}", "ERROR")
        return {"code": 500, "message": str(e)}


@app.route("/")
def index():
    config = load_config()
    return render_template("index.html", config=config,
                           platforms=list(ADAPTERS))


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({"success": True, "config": load_config(),
                    "platforms": list(ADAPTERS)})


@app.route("/api/config", methods=["POST"])
def update_config():
    try:
        data = request.get_json()
        config = {
            "kw": data.get("kw", "1"),
            "include": data.get("include", []),
            "exclude": data.get("exclude", []),
            "count": data.get("count", 5),
            "platform": data.get("platform", "quark"),
        }
        if data.get("permanent"):
            save_config(config)
            return jsonify({"success": True, "message": "配置已永久保存"})
        return jsonify({"success": True, "message": "临时配置已生效"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/preview", methods=["POST"])
def preview_task():
    try:
        data = request.get_json()
        platform = data.get("platform", "quark")
        kw = data.get("kw")
        include = data.get("include", [])
        exclude = data.get("exclude", [])
        count = data.get("count", 5)
        if isinstance(include, str):
            include = [x.strip() for x in include.split(",") if x.strip()]
        if isinstance(exclude, str):
            exclude = [x.strip() for x in exclude.split(",") if x.strip()]

        # 百度无预览机制，直接执行
        if platform == "baidu":
            return jsonify({"success": False,
                            "message": "百度网盘暂不支持预览，请直接运行"}), 400

        log_run(f"开始预览任务: platform={platform}, kw={kw}, count={count}")
        result = run_adapter(platform, kw, include, exclude, count,
                             preview_only=True)
        if result.get("code") == 200 and result.get("items"):
            save_preview_cache(result.get("items"),
                               {"kw": kw, "include": include,
                                "exclude": exclude, "count": count,
                                "platform": platform})
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/confirm", methods=["POST"])
def confirm_task():
    try:
        data = load_preview_cache()
        if not data or not data.get("items"):
            return jsonify({"success": False,
                            "message": "没有可用的预览数据，请先预览"}), 400
        items = data.get("items")
        config = data.get("config", {})
        cached_platform = config.get("platform", "quark")
        count = config.get("count", len(items))

        # 预览返回的就是完整资源对象（夸克），百度不支持确认流程
        def run_in_thread():
            result = run_adapter(cached_platform, config.get("kw"),
                                 config.get("include", []),
                                 config.get("exclude", []),
                                 count, selected_items=items)
            clear_preview_cache()
            log_run(f"确认任务完成: {result.get('message')}")

        t = threading.Thread(target=run_in_thread)
        t.start()
        return jsonify({"success": True,
                        "message": f"任务已启动（{cached_platform}），"
                                   f"使用预览资源 {len(items)} 个"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def run_task():
    try:
        data = request.get_json()
        platform = data.get("platform", "quark")
        kw = data.get("kw", "1")
        include = data.get("include", [])
        exclude = data.get("exclude", [])
        count = data.get("count", 5)
        permanent = data.get("permanent", False)
        if isinstance(include, str):
            include = [x.strip() for x in include.split(",") if x.strip()]
        if isinstance(exclude, str):
            exclude = [x.strip() for x in exclude.split(",") if x.strip()]

        if permanent:
            save_config({"kw": kw, "include": include, "exclude": exclude,
                         "count": count, "platform": platform})

        def run_in_thread():
            result = run_adapter(platform, kw, include, exclude, count)
            log_run(f"任务完成: {result.get('message')}")

        t = threading.Thread(target=run_in_thread)
        t.start()
        return jsonify({"success": True,
                        "message": f"任务已启动（{platform}），数量: {count}",
                        "config": {"kw": kw, "include": include,
                                   "exclude": exclude, "count": count,
                                   "platform": platform}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/logs", methods=["GET"])
def get_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-100:]
            return jsonify({"success": True, "logs": "".join(lines)})
        return jsonify({"success": True, "logs": "暂无日志"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def start_web(port=5003):
    ensure_dirs()
    print("=" * 60)
    print("网盘拉新 Web 管理界面（百度 + 夸克）")
    print(f"访问地址: http://localhost:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    start_web()