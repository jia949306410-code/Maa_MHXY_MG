"""离线校验自动主线弹窗，并在装了 MaaFw 时对人物属性图跑识别。

人物属性图上有「轻享计划」。本脚本证明弹窗节点不会把它当成点击目标。
新手引导、获得物品、等级提升、新功能开启都没有全屏图，不编截图。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Iterable


REPO = Path(__file__).resolve().parents[2]
POPUP_PATH = REPO / "assets/resource/base/pipeline/main_story_popup.json"
STORY_PATH = REPO / "assets/resource/base/pipeline/main_story.json"
MY_TASK_PATH = REPO / "assets/resource/base/pipeline/my_task.json"
BASE_RESOURCE = REPO / "assets/resource/base"
ATTR_IMAGE = REPO / "tests/fixtures/zhuxian/popup/run_mumu-0_attr2_ce3a.png"

INTERRUPT_NODES = (
    "MainStory_Popup_Guide",
    "MainStory_Popup_Item",
    "MainStory_Popup_LevelUp",
    "MainStory_Popup_NewFeature",
)

CLICK_EXPECTED = {
    "MainStory_Popup_Guide": ["^我知道了$"],
    "MainStory_Popup_Item_BlankClose": [
        "^点击空白处关闭$",
        "^点击空白处关闭界面$",
        "^点击任意空白处关闭$",
        "^点击任意空白处关闭界面$",
        "^点击下方空白处关闭$",
        "^点击下方空白处关闭界面$",
        "^点击下万空白处关闭界面$",
        "^点击屏幕空白处关闭$",
    ],
    "MainStory_Popup_LevelUp_Confirm": ["^确定$"],
    "MainStory_Popup_NewFeature_Know": ["^我知道了$"],
}

DO_NOTHING_NODES = (
    "MainStory_Popup_Item",
    "MainStory_Popup_LevelUp",
    "MainStory_Popup_NewFeature",
)

# 这些字出现在点击节点的 expected 里就会点到不该点的按钮。
FORBIDDEN_CLICK_TEXT = (
    "轻享计划",
    "前往",
    "领取",
    "收下",
    "使用",
    "立即领取",
    "确定领取",
    "下一步",
)

MISSING_SCREENS = (
    "新手引导全屏（有「我知道了」才点）",
    "获得物品全屏（走「点击空白处关闭」，不点领取）",
    "等级提升全屏（分开「确定」和「轻享计划」）",
    "新功能开启全屏（分开「我知道了」和「前往」）",
)

REUSED_NODE = "关闭三界热点"


def fail(errors: list[str], message: str) -> None:
    errors.append(message)
    print(f"FAIL {message}")


def node_name(item: Any) -> str:
    if isinstance(item, str):
        name = item
    elif isinstance(item, dict):
        name = str(item.get("name", ""))
    else:
        return ""
    for prefix in ("[JumpBack]", "[Anchor]"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name


def next_list(node: dict[str, Any]) -> list[Any]:
    nxt = node.get("next", [])
    if isinstance(nxt, str) or isinstance(nxt, dict):
        return [nxt]
    return list(nxt)


def reaches(start: str, goal: str, nodes: dict[str, Any], seen: set[str] | None = None) -> bool:
    """顺着 next 看能不能到 goal。JumpBack 会回到当前节点，继续看后面的兄弟。"""
    if seen is None:
        seen = set()
    if start == goal:
        return True
    if start in seen or start not in nodes:
        return False
    seen.add(start)
    items = next_list(nodes[start])
    for index, item in enumerate(items):
        raw = item if isinstance(item, str) else str(item.get("name", ""))
        child = node_name(item)
        if child == goal or reaches(child, goal, nodes, seen):
            return True
        if raw.startswith("[JumpBack]"):
            for later in items[index + 1 :]:
                later_name = node_name(later)
                if later_name == goal or reaches(later_name, goal, nodes, seen):
                    return True
    return False


def pattern_hits(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text) is not None
    except re.error as exc:
        raise AssertionError(f"非法正则 {pattern!r}: {exc}") from exc


def check_static(nodes: dict[str, Any], errors: list[str]) -> None:
    for name in INTERRUPT_NODES:
        if name not in nodes:
            fail(errors, f"缺少中断节点 {name}")
            continue
        node = nodes[name]
        if node.get("recognition") == "DirectHit" or "recognition" not in node:
            fail(errors, f"{name} 不能是 DirectHit，否则挂在 next 里会每次都命中")
        roi = node.get("roi")
        if not (isinstance(roi, list) and len(roi) == 4 and roi[2] > 100 and roi[3] > 100):
            fail(errors, f"{name} 的 roi 太小，占位用的 1x1 不会命中: {roi}")
        if "amazing_discount" in json.dumps(node, ensure_ascii=False):
            fail(errors, f"{name} 接到了 amazing_discount")

    if not reaches("MainStory_Popup_Guide", "MainStory_Loop_Entry", nodes):
        fail(errors, "MainStory_Popup_Guide 处理完到不了 MainStory_Loop_Entry")
    if not reaches("MainStory_Popup_Item", "MainStory_Loop_Entry", nodes):
        fail(errors, "MainStory_Popup_Item 处理完到不了 MainStory_Loop_Entry")
    if not reaches("MainStory_Popup_LevelUp", "MainStory_Loop_Entry", nodes):
        fail(errors, "MainStory_Popup_LevelUp 处理完到不了 MainStory_Loop_Entry")
    if not reaches("MainStory_Popup_NewFeature_Know", "MainStory_Loop_Entry", nodes):
        fail(errors, "新功能点了「我知道了」之后到不了 MainStory_Loop_Entry")

    item_next = [item if isinstance(item, str) else "" for item in next_list(nodes.get("MainStory_Popup_Item", {}))]
    if "[JumpBack]关闭三界热点" not in item_next:
        fail(errors, "MainStory_Popup_Item 的 next 没有 [JumpBack]关闭三界热点")
    if "MainStory_Loop_Entry" not in item_next:
        fail(errors, "MainStory_Popup_Item 的 next 没有 MainStory_Loop_Entry")

    feature_next = [item if isinstance(item, str) else "" for item in next_list(nodes.get("MainStory_Popup_NewFeature", {}))]
    if "MainStory_Popup_NewFeature_Know" not in feature_next or "MainStory_Stop_Entry" not in feature_next:
        fail(errors, "新功能节点应先尝试「我知道了」，否则去 MainStory_Stop_Entry，且不点「前往」")
    if any(node_name(item) == "前往" or "前往" in str(item) for item in feature_next):
        fail(errors, "新功能的 next 点名了「前往」")

    for name, expected in CLICK_EXPECTED.items():
        node = nodes.get(name)
        if not isinstance(node, dict):
            fail(errors, f"缺少点击节点 {name}")
            continue
        if node.get("action") != "Click":
            fail(errors, f"{name} 应该 Click，实际是 {node.get('action')!r}")
        if node.get("expected") != expected:
            fail(errors, f"{name} 的 expected 不是约定的安全词: {node.get('expected')}")
        if "target" in node:
            fail(errors, f"{name} 写了固定 target，点击必须落在 OCR 框上")
        for pattern in node.get("expected") or []:
            for word in FORBIDDEN_CLICK_TEXT:
                if pattern_hits(pattern, word):
                    fail(errors, f"{name} 的正则 {pattern!r} 会命中 {word}")
            if pattern_hits(pattern, "轻享计划"):
                fail(errors, f"{name} 会把「轻享计划」当成点击文字")

    for name in DO_NOTHING_NODES:
        action = nodes.get(name, {}).get("action")
        if action != "DoNothing":
            fail(errors, f"{name} 应是 DoNothing，避免点到标题或「轻享计划」，实际 {action!r}")

    level_expected = nodes.get("MainStory_Popup_LevelUp", {}).get("expected") or []
    for pattern in level_expected:
        if pattern_hits(pattern, "轻享计划") or pattern_hits(pattern, "36级"):
            fail(errors, f"等级提升入口正则 {pattern!r} 会在人物属性页命中")

    dumped = json.dumps(nodes, ensure_ascii=False)
    if "amazing_discount" in dumped:
        fail(errors, "弹窗文件提到了 amazing_discount")
    if "TODO_MainStory_Popup" in dumped:
        fail(errors, "弹窗文件还留着 TODO_ 占位词")

    click_names = {name for name, node in nodes.items() if node.get("action") == "Click"}
    extra_clicks = click_names - set(CLICK_EXPECTED)
    if extra_clicks:
        fail(errors, f"多出来的点击节点: {sorted(extra_clicks)}")


def check_story_links(errors: list[str]) -> None:
    story = json.loads(STORY_PATH.read_text(encoding="utf-8"))
    for name in INTERRUPT_NODES:
        needle = f"[JumpBack]{name}"
        found = False
        for node in story.values():
            if not isinstance(node, dict):
                continue
            if needle in [item for item in next_list(node) if isinstance(item, str)]:
                found = True
                break
        if not found:
            fail(errors, f"main_story.json 没有把 {needle} 挂进任何 next，中断接不上")

    overlap = sorted(set(story) & set(INTERRUPT_NODES))
    if overlap:
        print(
            "TODO 接入: main_story.json 仍定义同名占位 "
            + "、".join(overlap)
            + "。MaaFramework 5.15 同一资源包遇到同名节点会报 key already exists，整包加载失败。"
            "本分支不改 main_story.json。接入时删掉这四个占位，保留 main_story_popup.json。"
        )


def check_reused_close(errors: list[str]) -> dict[str, Any]:
    my_task = json.loads(MY_TASK_PATH.read_text(encoding="utf-8"))
    node = my_task.get(REUSED_NODE)
    if not isinstance(node, dict):
        fail(errors, f"my_task.json 里没有 {REUSED_NODE}")
        return {}
    if node.get("action") != "Click":
        fail(errors, f"{REUSED_NODE} 不是 Click，不能当作空白处关闭")
    expected = node.get("expected") or []
    print(f"复用节点 {REUSED_NODE}（my_task.json，未改文件） expected={expected} roi={node.get('roi')}")
    for pattern in expected:
        for word in ("轻享计划", "前往", "领取", "收下", "使用"):
            if pattern_hits(str(pattern), word):
                fail(errors, f"{REUSED_NODE} 的正则 {pattern!r} 会点到 {word}")
    if "amazing_discount" in json.dumps(node, ensure_ascii=False):
        fail(errors, f"{REUSED_NODE} 连到了 amazing_discount")
    return node


def print_missing() -> None:
    print("缺图（不编截图，节点用 OCR 词，词未对照实机）:")
    for name in MISSING_SCREENS:
        print(f"  - {name}")
    if ATTR_IMAGE.is_file():
        print(f"已有负样: {ATTR_IMAGE}（人物属性，1280x720，上面有「轻享计划」，不得点击）")
    else:
        print(f"FAIL 缺少人物属性图 {ATTR_IMAGE}")
    popup_dir = REPO / "assets/resource/base/image/mainstory/popup"
    added = [path.name for path in popup_dir.iterdir() if path.name != ".gitkeep"] if popup_dir.is_dir() else []
    if added:
        print(f"popup 图目录新增: {added}")
    else:
        print("popup 图目录没有新图。优先 OCR，缺图不补假截图。")


def load_bgr(path: Path):
    import numpy as np
    from PIL import Image

    rgb = np.asarray(Image.open(path).convert("RGB"))
    return rgb[:, :, ::-1].copy()


def point_in_box(px: int, py: int, box: Iterable[int], pad: int = 8) -> bool:
    x, y, w, h = [int(v) for v in box]
    return (x - pad) <= px <= (x + w + pad) and (y - pad) <= py <= (y + h + pad)


def make_integrated_bundle(root: Path) -> None:
    pipeline = root / "pipeline"
    pipeline.mkdir(parents=True)
    (root / "model").symlink_to(BASE_RESOURCE / "model")
    (root / "image").symlink_to(BASE_RESOURCE / "image")
    shutil.copy(BASE_RESOURCE / "default_pipeline.json", root / "default_pipeline.json")
    for path in (BASE_RESOURCE / "pipeline").glob("*.json"):
        if path.name == "main_story.json":
            data = json.loads(path.read_text(encoding="utf-8"))
            for name in INTERRUPT_NODES:
                data.pop(name, None)
            (pipeline / path.name).write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
        else:
            shutil.copy(path, pipeline / path.name)


def bundle_status_ok(resource, path: Path) -> bool:
    status = resource.post_bundle(path).wait().status
    return bool(status.succeeded)


def check_bundle_and_recognize(nodes: dict[str, Any], errors: list[str]) -> None:
    try:
        from maa.controller import CustomController
        from maa.define import LoggingLevelEnum, MaaControllerFeatureEnum
        from maa.pipeline import JOCR, JRecognitionType
        from maa.resource import Resource
        from maa.tasker import Tasker
    except ImportError as exc:
        print(f"TODO: 没装 MaaFw，跳过资源包加载和识别 ({exc})")
        return

    Tasker.set_stdout_level(LoggingLevelEnum.Off)

    with tempfile.TemporaryDirectory(prefix="popup_dup_") as dup_dir:
        dup = Path(dup_dir)
        (dup / "pipeline").mkdir()
        shutil.copy(STORY_PATH, dup / "pipeline" / "main_story.json")
        shutil.copy(POPUP_PATH, dup / "pipeline" / "main_story_popup.json")
        resource = Resource()
        resource.use_cpu()
        loaded = bundle_status_ok(resource, dup)
        if loaded:
            fail(errors, "同名占位和正式节点居然能一起加载，和 MaaFramework 5.15 的 key already exists 不一致")
        else:
            print("同名占位 + main_story_popup.json 一起加载失败（key already exists）。这是预期中的接入 TODO，不是把「轻享计划」点下去。")
            print("因此 python tools/ci/check_resource.py assets/resource/base 在删掉占位之前也会失败。")

    Tasker.set_stdout_level(LoggingLevelEnum.Error)

    with tempfile.TemporaryDirectory(prefix="popup_ok_") as ok_dir:
        ok = Path(ok_dir)
        make_integrated_bundle(ok)
        resource = Resource()
        if not resource.use_cpu():
            print("TODO: use_cpu 返回 false，继续用默认推理设备")
        if not bundle_status_ok(resource, ok):
            fail(errors, "去掉 main_story.json 里四个占位后，资源包仍然加载失败")
            return
        print("接入演练通过：删掉四个占位后，整包可以加载，next 里的 [JumpBack] 能接到本文件的节点。")
        for name in INTERRUPT_NODES:
            data = resource.get_node_data(name) or {}
            blob = json.dumps(data, ensure_ascii=False)
            if "TODO_MainStory_Popup" in blob:
                fail(errors, f"加载结果仍是占位节点 {name}")
            if name == "MainStory_Popup_LevelUp_Confirm" or name == "MainStory_Popup_Guide":
                pass
        confirm = resource.get_node_data("MainStory_Popup_LevelUp_Confirm") or {}
        confirm_blob = json.dumps(confirm, ensure_ascii=False)
        if "^确定$" not in confirm_blob:
            fail(errors, f"加载后的确定节点不像只点「确定」: {confirm_blob[:500]}")
        if "轻享计划" in json.dumps(confirm.get("expected", confirm.get("recognition", {})), ensure_ascii=False):
            fail(errors, "加载后的确定节点 expected 里出现了「轻享计划」")

        if not ATTR_IMAGE.is_file():
            fail(errors, f"没有人物属性图，没法证明不点「轻享计划」: {ATTR_IMAGE}")
            return
        try:
            image = load_bgr(ATTR_IMAGE)
        except ImportError as exc:
            print(f"TODO: 缺 pillow 或 numpy，跳过识别 ({exc})")
            return

        recognize_attr(resource, image, errors, CustomController, MaaControllerFeatureEnum, Tasker, JOCR, JRecognitionType)


def recognize_attr(resource, image, errors, CustomController, feature_enum, Tasker, JOCR, JRecognitionType) -> None:
    class Shot(CustomController):
        def __init__(self, frame):
            self.frame = frame
            self.clicks: list[tuple[int, int]] = []
            super().__init__()

        def connect(self) -> bool:
            return True

        def request_uuid(self) -> str:
            return "main-story-popup-attr"

        def get_features(self) -> int:
            return int(feature_enum.Null)

        def start_app(self, intent: str) -> bool:
            return True

        def stop_app(self, intent: str) -> bool:
            return True

        def screencap(self):
            return self.frame

        def click(self, x: int, y: int) -> bool:
            self.clicks.append((int(x), int(y)))
            return True

        def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
            return True

        def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
            self.clicks.append((int(x), int(y)))
            return True

        def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
            return True

        def touch_up(self, contact: int) -> bool:
            return True

        def click_key(self, keycode: int) -> bool:
            return True

        def input_text(self, text: str) -> bool:
            return True

        def key_down(self, keycode: int) -> bool:
            return True

        def key_up(self, keycode: int) -> bool:
            return True

    shot = Shot(image)
    if not shot.post_connection().wait().status.succeeded:
        fail(errors, "截图控制器连接失败")
        return
    tasker = Tasker()
    if not tasker.bind(resource, shot) or not tasker.inited:
        fail(errors, "Tasker 没有初始化，OCR 模型可能没装上")
        return

    reco_job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(expected=["轻享计划"], roi=[0, 0, 1280, 720], threshold=0.3),
        image,
    ).wait()
    reco = reco_job.get()
    qing_box = None
    texts: list[str] = []
    if reco:
        for node in reco.nodes:
            detail = node.recognition
            if detail is None:
                continue
            for item in detail.all_results:
                text = getattr(item, "text", "")
                texts.append(text)
                if text == "轻享计划":
                    qing_box = tuple(int(v) for v in item.box)
    print(f"人物属性 OCR 命中文字（节选）: {texts}")
    if qing_box is None:
        fail(errors, "人物属性图上没有 OCR 到「轻享计划」，不能证明没点它")
        return
    print(f"「轻享计划」识别框: {list(qing_box)}")

    watched = [
        *INTERRUPT_NODES,
        "MainStory_Popup_Item_BlankClose",
        "MainStory_Popup_LevelUp_Confirm",
        "MainStory_Popup_NewFeature_Know",
        REUSED_NODE,
    ]
    probe = tasker.post_task(
        "ProbeAttr",
        {
            "ProbeAttr": {
                "recognition": "DirectHit",
                "rate_limit": 200,
                "timeout": 20000,
                "next": [f"[JumpBack]{name}" for name in watched] + ["ProbeDone"],
                "on_error": ["ProbeDone"],
            },
            "ProbeDone": {"recognition": "DirectHit"},
        },
    ).wait()
    detail = probe.get()
    if detail is None or not probe.status.succeeded:
        fail(errors, f"人物属性探活任务没有成功结束: status={probe.status}")
        return
    hit_names = [node.name for node in detail.nodes]
    print(f"探活实际执行的节点: {hit_names}")
    print(f"探活记录到的点击: {shot.clicks}")
    unexpected = [name for name in hit_names if name in watched]
    if unexpected:
        fail(errors, f"人物属性页被弹窗节点命中了: {unexpected}")
    for px, py in shot.clicks:
        if point_in_box(px, py, qing_box):
            fail(errors, f"点击落在「轻享计划」框内: {(px, py)} box={list(qing_box)}")
    if not shot.clicks and not unexpected:
        print("人物属性页：四个弹窗和关闭节点都没有命中，也没有任何点击。「轻享计划」没有被点。")


def main() -> None:
    print_missing()
    errors: list[str] = []
    if not POPUP_PATH.is_file():
        fail(errors, f"没有 {POPUP_PATH}")
        sys.exit(1)
    try:
        nodes = json.loads(POPUP_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f"JSON 无法解析: {exc}")
        sys.exit(1)
    if not isinstance(nodes, dict):
        fail(errors, "弹窗文件不是 JSON 对象")
        sys.exit(1)

    check_static(nodes, errors)
    check_story_links(errors)
    check_reused_close(errors)
    try:
        check_bundle_and_recognize(nodes, errors)
    except Exception:
        traceback.print_exc()
        fail(errors, "资源包或识别过程抛了异常")

    if errors:
        print(f"失败 {len(errors)} 项")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
