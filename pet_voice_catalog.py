"""Subset of Tencent's TextToVoice voice table, verified 2026-10-26.

Source: https://cloud.tencent.com/document/product/1073/92668
Categories are upstream categories, not inferred from ID ranges.
"""
VOICE_GROUPS = {
    "精品音色": ((101001, "智瑜"), (101015, "智萌"), (101016, "智甜"),
                 (101004, "智云"), (101026, "智希"), (101027, "智梅"),
                 (101030, "智柯"), (101054, "智友"), (101055, "智付")),
    "超自然大模型音色": ((402000, "云晓芙"), (402001, "云小团"), (402002, "云大围"),
                       (403000, "云小朵"), (403001, "云小和"), (403008, "云小洋"),
                       (603002, "软萌心心"), (603004, "温柔小柠"), (603007, "邻家女孩")),
    "大模型音色": ((501000, "智斌"), (501004, "月华"), (501005, "飞镜"),
                   (501007, "浅草"), (601008, "爱小豪"), (601009, "爱小芊")),
}
CHINESE_VOICES = tuple(item for group in VOICE_GROUPS.values() for item in group)


def voice_category(voice_id):
    return next((category for category, voices in VOICE_GROUPS.items()
                 if any(number == voice_id for number, _ in voices)), "")


def voice_label(voice_id):
    for category, voices in VOICE_GROUPS.items():
        for number, name in voices:
            if number == voice_id:
                return f"{name} · {number} · {category}"
    return str(voice_id)
