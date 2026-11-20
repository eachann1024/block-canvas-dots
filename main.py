#!/usr/bin/env python3
"""Official block-canvas-dots plugin for Block (blockmd)."""

import json
import re
import sys

STORAGE_KEY = "config"
SETTINGS_PAGE_SURFACE = "settings_page"
SETTINGS_SECTION_SURFACE = "settings_section:editor-background"
SUPPORTED_SURFACES = {SETTINGS_PAGE_SURFACE, SETTINGS_SECTION_SURFACE}

GRID_MODES = ("off", "grid", "dots")
GRID_STRENGTHS = ("standard", "high")
LEGACY_STRENGTH_MAP = {
    "normal": "standard",
    "strong": "high",
}
DEFAULT_CONFIG = {"grid_mode": "dots", "grid_strength": "standard"}

CHINESE_TEXT = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")

TEXTS = {
    "zh": {
        "page_title": "编辑器背景",
        "card_title": "背景样式",
        "reset_default": "恢复默认",
        "reset_done": "已恢复默认设置",
        "grid_mode_label": "背景网格",
        "grid_modes": {"off": "无", "grid": "方格", "dots": "点阵"},
        "grid_strength_label": "对比度",
        "grid_strengths": {"standard": "标准", "high": "高对比"},
        "unknown_action": "未知操作：{action}",
        "unsupported_surface": "不支持的界面：{surface}",
        "unsupported_method": "不支持的方法：{method}",
    },
    "en": {
        "page_title": "Editor Background",
        "card_title": "Background Style",
        "reset_default": "Reset Default",
        "reset_done": "Reset to default settings",
        "grid_mode_label": "Background Grid",
        "grid_modes": {"off": "Off", "grid": "Grid", "dots": "Dots"},
        "grid_strength_label": "Contrast",
        "grid_strengths": {"standard": "Standard", "high": "High"},
        "unknown_action": "Unknown action: {action}",
        "unsupported_surface": "Unsupported surface: {surface}",
        "unsupported_method": "Unsupported method: {method}",
    },
}


class HostRPCError(RuntimeError):
    """JSON-RPC error returned by the Block host."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class HostBridge:
    """Line-delimited JSON-RPC 2.0 bridge between plugin and Block host."""

    def __init__(self):
        self._seq = 1000

    def send(self, obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def call_host(self, method, params=None):
        self._seq += 1
        req_id = f"p-{self._seq}"
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        while True:
            line = sys.stdin.readline()
            if not line:
                raise RuntimeError("stdin closed while awaiting host response")
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if str(msg.get("id")) == req_id:
                if msg.get("error"):
                    err = msg["error"]
                    raise HostRPCError(int(err.get("code", -32000)), str(err.get("message", "host error")))
                return msg.get("result", {})

    def serve(self, handler, localize_error):
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            method = req.get("method")
            req_id = req.get("id")
            try:
                result = handler(self, method, req.get("params") or {})
                self.send({"jsonrpc": "2.0", "id": req_id, "result": result})
                if method == "plugin.shutdown":
                    break
            except Exception as exc:
                self.send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": localize_error(str(exc))},
                })


class CanvasDotsPlugin:
    """Manages native editor background mode and contrast for Block."""

    def __init__(self):
        self.locale = "zh"

    def _is_en(self):
        return str(self.locale or "").strip().lower().startswith("en")

    def text(self):
        return TEXTS["en"] if self._is_en() else TEXTS["zh"]

    def _localize_error(self, message):
        text = str(message or "").strip()
        if self._is_en() and CHINESE_TEXT.search(text):
            return "Operation failed"
        return text or ("Operation failed" if self._is_en() else "操作失败")

    @staticmethod
    def normalize(cfg):
        out = dict(DEFAULT_CONFIG)
        if not isinstance(cfg, dict):
            return out
        mode = str(cfg.get("grid_mode") or "").strip().lower()
        if mode in GRID_MODES:
            out["grid_mode"] = mode
        strength = str(cfg.get("grid_strength") or "").strip().lower()
        strength = LEGACY_STRENGTH_MAP.get(strength, strength)
        if strength in GRID_STRENGTHS:
            out["grid_strength"] = strength
        return out

    def merge_values(self, cfg, values):
        if not isinstance(values, dict):
            return self.normalize(cfg)
        merged = dict(cfg)
        for key in ("grid_mode", "grid_strength"):
            if isinstance(values.get(key), str):
                merged[key] = values[key]
        return self.normalize(merged)

    def load_config(self, bridge):
        try:
            res = bridge.call_host("host.storage.get", {"key": STORAGE_KEY})
            if isinstance(res, dict) and res.get("found") and isinstance(res.get("value"), dict):
                return self.normalize(res["value"])
        except HostRPCError as exc:
            if exc.code != -32601:
                raise
        return dict(DEFAULT_CONFIG)

    def save_config(self, bridge, cfg):
        try:
            bridge.call_host("host.storage.set", {"key": STORAGE_KEY, "value": cfg})
        except HostRPCError as exc:
            if exc.code != -32601:
                raise

    def sync_host_background(self, bridge, cfg):
        try:
            return bridge.call_host(
                "host.ui.set_editor_background",
                {"mode": cfg["grid_mode"], "contrast": cfg["grid_strength"]},
            )
        except HostRPCError as exc:
            if exc.code == -32601:
                return None
            raise

    def refresh_other_surface(self, bridge, current_surface):
        other = SETTINGS_SECTION_SURFACE if current_surface == SETTINGS_PAGE_SURFACE else SETTINGS_PAGE_SURFACE
        try:
            bridge.call_host("host.ui.refresh", {"surface": other})
        except HostRPCError:
            pass

    def _validate_surface(self, params):
        surface = str((params or {}).get("surface") or SETTINGS_PAGE_SURFACE).strip() or SETTINGS_PAGE_SURFACE
        if surface not in SUPPORTED_SURFACES:
            raise RuntimeError(self.text()["unsupported_surface"].format(surface=surface))
        return surface

    def build_page(self, surface, cfg, status=""):
        t = self.text()
        card_title = "" if surface == SETTINGS_SECTION_SURFACE else t["card_title"]
        return {
            "title": t["page_title"],
            "status": status,
            "header_actions": [
                {"id": "reset_default", "label": t["reset_default"], "variant": "secondary"},
            ],
            "cards": [
                {
                    "id": "canvas-card",
                    "title": card_title,
                    "items": [
                        {
                            "kind": "segmented",
                            "id": "grid_mode",
                            "label": t["grid_mode_label"],
                            "value": cfg["grid_mode"],
                            "options": [{"value": k, "label": t["grid_modes"][k]} for k in GRID_MODES],
                        },
                        {
                            "kind": "segmented",
                            "id": "grid_strength",
                            "label": t["grid_strength_label"],
                            "value": cfg["grid_strength"],
                            "options": [{"value": k, "label": t["grid_strengths"][k]} for k in GRID_STRENGTHS],
                        },
                    ],
                }
            ],
        }

    def handle(self, bridge, method, params):
        if method == "plugin.initialize":
            locale = str(params.get("locale") or "").strip().lower()
            self.locale = "en" if locale.startswith("en") else "zh"
            cfg = self.load_config(bridge)
            self.sync_host_background(bridge, cfg)
            return {"ready": True}

        if method == "plugin.render":
            surface = self._validate_surface(params)
            cfg = self.load_config(bridge)
            values = params.get("values")
            if isinstance(values, dict) and any(k in values for k in ("grid_mode", "grid_strength")):
                next_cfg = self.merge_values(cfg, values)
                changed = next_cfg != cfg
                cfg = next_cfg
                self.save_config(bridge, cfg)
                self.sync_host_background(bridge, cfg)
                if changed:
                    self.refresh_other_surface(bridge, surface)
            return {"page": self.build_page(surface, cfg), "values": cfg}

        if method == "plugin.action":
            surface = self._validate_surface(params)
            t = self.text()
            action_id = str(params.get("action_id") or "").strip()
            if action_id == "reset_default":
                cfg = dict(DEFAULT_CONFIG)
                self.save_config(bridge, cfg)
                self.sync_host_background(bridge, cfg)
                self.refresh_other_surface(bridge, surface)
                status = t["reset_done"]
                return {
                    "ok": True,
                    "tone": "success",
                    "status": status,
                    "values": cfg,
                    "page": self.build_page(surface, cfg, status=status),
                }

            cfg = self.load_config(bridge)
            status = t["unknown_action"].format(action=action_id)
            return {
                "ok": False,
                "tone": "warning",
                "status": status,
                "values": cfg,
                "page": self.build_page(surface, cfg),
            }

        if method == "plugin.event":
            event_name = str(params.get("event") or "")
            if event_name == "locale.changed" and params.get("locale"):
                locale = str(params["locale"]).strip().lower()
                self.locale = "en" if locale.startswith("en") else "zh"
                return {"handled": True}
            return {"handled": False}

        if method == "plugin.shutdown":
            return {"ok": True}

        raise RuntimeError(self.text()["unsupported_method"].format(method=method))


def main():
    plugin = CanvasDotsPlugin()
    HostBridge().serve(plugin.handle, plugin._localize_error)


if __name__ == "__main__":
    main()
