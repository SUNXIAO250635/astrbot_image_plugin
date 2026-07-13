from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    prompt_suffix: str
    reference_preferred: bool = False

    def apply(self, prompt: str) -> str:
        prompt = (prompt or "").strip()
        return f"{prompt}。{self.prompt_suffix}" if prompt else self.prompt_suffix


PRESETS = {
    "头像": Preset(
        "头像",
        "适合作为头像，主体清晰居中，边缘保留安全空间，构图适配当前输出画幅",
        reference_preferred=True,
    ),
    "海报": Preset("海报", "海报设计，信息层级清晰，预留标题排版空间"),
    "壁纸": Preset("壁纸", "桌面壁纸设计，画面完整，主体布局适配当前输出画幅"),
    "卡片": Preset("卡片", "卡片设计，边界清晰，信息层级明确，构图适配当前输出画幅"),
    "手机壁纸": Preset(
        "手机壁纸", "手机壁纸设计，主体避开图标区域，布局适配当前输出画幅"
    ),
    "手办化": Preset(
        "手办化",
        "转换为精致实体收藏手办，保留主体身份特征，展示材质、底座与真实棚拍光线",
        reference_preferred=True,
    ),
    "表情包": Preset(
        "表情包",
        "表情包贴纸风格，表情动作明确，粗黑描边，背景干净，文字区域清晰",
        reference_preferred=True,
    ),
    "风格转换": Preset(
        "风格转换",
        "保留原图主体、动作与构图，只转换用户指定的视觉风格",
        reference_preferred=True,
    ),
}


PRESET_ALIASES = {
    "手机": "手机壁纸",
    "手办": "手办化",
    "贴纸": "表情包",
    "转风格": "风格转换",
}


def get_preset(value: str) -> Preset | None:
    key = PRESET_ALIASES.get((value or "").strip(), (value or "").strip())
    return PRESETS.get(key)


def detect_preset(prompt: str) -> Preset | None:
    text = prompt or ""
    for key in ("手机壁纸", "风格转换", "手办化", "表情包", "头像", "海报", "壁纸", "卡片"):
        if key in text:
            return PRESETS[key]
    for alias, key in PRESET_ALIASES.items():
        if alias in text:
            return PRESETS[key]
    return None
