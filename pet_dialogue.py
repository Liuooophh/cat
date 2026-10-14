"""Original short lines for the desktop bear, kept separate from the UI."""

from __future__ import annotations

import random
import time


DIALOGUE: dict[str, list[str]] = {
    "idle": [
        "嗯……今天吃点啥呢？(￣﹃￣)",
        "你盯我半天了，有事直说。",
        "我在想一个大问题：午饭吃几顿。",
        "好困啊，先把眼睛借我眯一会儿。",
        "零食放哪儿了？我就确认一下。",
        "别看了，我今天不表演。",
        "这桌面挺大，适合躺平。",
        "我刚才想干啥来着……算了。",
        "安静一会儿也挺舒服。",
        "你忙你的，我负责看着这儿。",
    ],
    "happy": [
        "嘿，今天这事办得漂亮！",
        "心情不错，走路都想带点节奏。",
        "不错不错，值得加一顿夜宵。",
        "哈哈，今天还挺顺当！",
        "我就说嘛，事情总能捋顺。",
        "来，给你一个大哥式点头。",
        "这下舒坦了，肚子也跟着开心。",
        "好消息？那我先替你乐一会儿。",
    ],
    "angry": [
        "喂，戳够了没？我也有脾气的。",
        "再闹我可要把零食藏起来了！",
        "我的点心呢？这事得查清楚。",
        "哼，我现在有一点点不高兴。",
        "你先停手，咱们讲讲道理。",
        "我眉毛都皱起来了，你看见没？",
        "行了行了，再戳我就装没听见。",
        "我数到三……算了，我先冷静。",
    ],
    "laugh": [
        "哈哈哈，等会儿，我笑岔气了！",
        "哎哟，这一下真把我逗乐了。",
        "哈哈，肚皮都跟着抖起来了。",
        "不行了，让我缓一口气。",
        "这事我能笑到下一顿饭。",
        "哈哈哈，今天的快乐有着落了。",
    ],
    "yawn": [
        "啊——我先打个哈欠，别催。",
        "眼皮有点沉，借我醒醒神。",
        "哈啊……刚才那下差点把我下巴撑开。",
        "我不是困，我是在省电。",
        "先伸个懒腰，精神马上回来。",
        "这哈欠挺有气势吧？",
    ],
    "sleep": [
        "呼……我就眯一小会儿。",
        "别吵，我梦见一桌好吃的了。",
        "今天先歇到这儿，明儿再说。",
        "Zzz……给我留块点心……",
        "让我睡会儿，醒了继续当大哥。",
        "呼噜……这地儿躺着还挺合适。",
    ],
    "clicked": [
        "嗯？叫我啊？",
        "咋啦？我听着呢。",
        "有事你说，别光戳。",
        "又点我？我脸上有菜单吗？",
        "你手头挺忙啊，老找我报到。",
        "我在呢，别把我点出回声来。",
        "哎，轻点，我又不是按钮。",
        "找我有事？还是路过顺手戳一下？",
        "再戳我就要收点心出场费了。",
        "我听到了，声音不用开这么大。",
        "你这手速，挺适合抢最后一块饼干。",
        "好了好了，我看见你啦。",
    ],
    "surprised": [
        "啊？刚才那一下我没反应过来。",
        "啥情况？我再看一眼。",
        "哎？事情怎么突然拐弯了？",
        "不会吧，我眼睛还挺好使呢。",
        "等会儿，这发展有点出乎我意料。",
        "吓我一跳，心都快蹦到点心盒里了。",
    ],
    "confused": [
        "嗯？你刚才说的我得琢磨琢磨。",
        "啥意思？我脑袋转得有点慢。",
        "我好像懂了，又好像没懂。",
        "等下，我先把这事理顺。",
        "你这操作让我有点摸不着头。",
        "我先认真想想，别急着笑我。",
    ],
    "wave": [
        "嘿，忙完记得回来看看。",
        "早啊！我刚醒，精神还在路上。",
        "回头见，给我留个好位置。",
        "拜啦，路上慢点儿。",
        "来，挥个手，排面得有。",
    ],
    "interaction": [
        "来，坐会儿，歇口气再忙。",
        "今天辛苦啦，我替你看一会儿桌面。",
        "要不要聊两句？我正好醒着。",
        "你忙累了就停一下，别硬撑。",
        "我这儿挺安静，适合发会儿呆。",
        "一块儿歇会儿，事情等下再做。",
        "你负责忙，我负责给你加油。",
        "好啦，今天也算一起并肩作战了。",
    ],
    "weather_rain": [
        "外头下雨了，出门记得带伞，别淋成落汤熊。",
        "雨天路滑，走路看着点儿，别急。",
        "这雨下得挺认真，你要出去可别忘了伞。",
    ],
    "weather_snow": [
        "外头下雪啦，记得穿暖和点，脚底也当心打滑。",
        "天冷得不轻，多套一件，别跟我一样只顾着嘴硬。",
        "这天气适合窝着喝热乎的，出门可要裹严实。",
    ],
    "weather_storm": [
        "外面雷雨挺凶，先在屋里待会儿，别急着出门。",
        "这雷声够响的，出门前看看天气，安全要紧。",
    ],
    "weather_hot": [
        "外头热得厉害，多喝水，别顶着太阳硬扛。",
        "这天热得我都想躲冰箱旁边了，你也记得避避暑。",
        "太阳挺毒的，出门记得防晒，水也带上。",
    ],
    "cpu": [
        "电脑忙得直喘气，咱先让它歇口气吧。",
        "CPU 都快冒烟啦，先关几个不用的窗口，别把它累着。",
        "你这电脑现在挺忙，俺先不催你，等它缓缓。",
    ],
    "time": [
        "都这么晚了还不歇？明天起不来可别赖我啊。",
        "后半夜啦，屏幕先放一边，睡够了再忙。",
        "这都几点了？我哈欠都打上了，你也早点休息。",
    ],
}


COOLDOWNS = {
    "idle": 45.0, "happy": 18.0, "angry": 8.0, "laugh": 18.0,
    "yawn": 35.0, "sleep": 90.0, "clicked": 1.4, "surprised": 15.0,
    "confused": 14.0, "wave": 30.0, "interaction": 20.0,
    "weather_rain": 7200.0, "weather_snow": 7200.0,
    "weather_storm": 7200.0, "weather_hot": 7200.0,
    "cpu": 1200.0, "time": 86400.0,
}


class DialogueManager:
    """Selects lines without immediate repeats and assigns readable display time."""

    def __init__(self) -> None:
        self._last_line: dict[str, str] = {}
        self._available_at: dict[str, float] = {}

    def choose(self, category: str, *, now: float | None = None,
               force: bool = False) -> tuple[str, float] | None:
        lines = DIALOGUE.get(category)
        if not lines:
            return None
        now = time.monotonic() if now is None else now
        if not force and now < self._available_at.get(category, 0.0):
            return None
        previous = self._last_line.get(category)
        choices = [line for line in lines if line != previous] or lines
        line = random.choice(choices)
        self._last_line[category] = line
        self._available_at[category] = now + COOLDOWNS.get(category, 12.0)
        duration = min(4.8, max(2.3, 1.65 + len(line) * 0.105))
        return line, duration
