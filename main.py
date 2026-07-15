import os

from maix import image, camera, display, uart, nn, app, touchscreen
from struct import pack

# 初始化检测器、摄像头和显示器
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(PROJECT_DIR, "models", "help_int8.mud")

# 开启硬件双缓冲，提高连续推理吞吐率
detector = nn.YOLOv8(model=MODEL_PATH, dual_buff=True)
labels = ["red", "yellow", "black", "blue", "red_area", "blue_area"]
BALL_LABELS = {"red", "yellow", "black", "blue"}
AREA_LABELS = {"blue_area", "red_area"}
cam = camera.Camera(640, 480, detector.input_format())
dis = display.Display()
ts = touchscreen.TouchScreen()

# UART初始化
serial = uart.UART("/dev/ttyS0", 115200)

# 界面元素配置
exit_label = "< Exit"
exit_size = image.string_size(exit_label)
exit_btn_pos = [0, 0, 8 * 2 + exit_size[0], 12 * 2 + exit_size[1]]

# OSD（屏幕信息显示）开关按钮，默认灰色（关闭）
color_buttons = [
    {"name": "Red", "pos": [640 - 67, 0, 67, 40], "selected": False, "color": image.COLOR_RED},
    {"name": "Blue", "pos": [640 - 67, 45, 67, 40], "selected": True, "color": image.COLOR_BLUE},
    {"name": "Strategy", "pos": [640 - 67, 90, 67, 40], "color": image.COLOR_GREEN, "strategy": "N"},
    {"name": "OSD", "pos": [640 - 67, 135, 67, 40], "color": image.COLOR_GRAY},
]

# 状态变量
current_team = "blue"  # 默认蓝方
target = "blue"        # 初始目标为蓝球
ball_count = 0          # 已抓球数量
safe_mode = False       # 是否在安全区模式
strategy = "N"         # 默认保守策略
now_status = 0x55
specified_x = 150
specified_y = 60
specified_w = 420
specified_h = 350
specified_x2 = specified_x + specified_w
specified_y2 = specified_y + specified_h
show_info = False       # 默认关闭调试绘制以提高帧率

# 预计算常量与缓存，减少主循环开销
COLOR_WHITE = image.COLOR_WHITE
COLOR_GRAY = image.COLOR_GRAY
COLOR_RED = image.COLOR_RED
COLOR_BLUE = image.COLOR_BLUE
COLOR_GREEN = image.COLOR_GREEN
STATUS_PKT_CACHE = {}
NONE_PKT_CACHE = {}


# 缓存固定状态帧，避免在主循环中重复打包
def make_status_pkt(status):
    pkt = STATUS_PKT_CACHE.get(status)
    if pkt is None:
        pkt = pack("<BBBBBBBBBBB", 0xaa, 0xbb, 0, 0, 0, 0, 0, 0, 0, status, 0xcc)
        STATUS_PKT_CACHE[status] = pkt
    return pkt


def make_none_pkt(status):
    pkt = NONE_PKT_CACHE.get(status)
    if pkt is None:
        pkt = pack("<BBBBBBBBBBB", 0xaa, 0xbb, 0, 0, 0, 0, 0x22, 0, 0, status, 0xcc)
        NONE_PKT_CACHE[status] = pkt
    return pkt


def is_in_button(x, y, btn_pos):
    return (btn_pos[0] <= x <= btn_pos[0] + btn_pos[2] and
            btn_pos[1] <= y <= btn_pos[1] + btn_pos[3])


def refresh_button_cache():
    """按钮文字尺寸/位置只在状态变化时重算。"""
    for btn in color_buttons:
        x, y, w, h = btn["pos"]
        text = btn["name"] if btn["name"] != "Strategy" else btn["strategy"]
        text_size = image.string_size(text)
        btn["text"] = text
        btn["tx"] = x + (w - text_size[0]) // 2
        btn["ty"] = y + (h - text_size[1]) // 2
        btn["border"] = COLOR_WHITE if btn.get("selected", False) else COLOR_GRAY


def draw_buttons(img):
    for btn in color_buttons:
        x, y, w, h = btn["pos"]
        img.draw_rect(x, y, w, h, color=btn["color"], thickness=-1)
        img.draw_rect(x, y, w, h, color=btn["border"], thickness=3)
        img.draw_string(btn["tx"], btn["ty"], btn["text"], color=COLOR_WHITE)
    img.draw_rect(*exit_btn_pos, color=COLOR_WHITE, thickness=2)
    img.draw_string(8, 12, exit_label, color=COLOR_WHITE)


def pick_largest(candidates):
    best = candidates[0]
    best_area = best.w * best.h
    for obj in candidates[1:]:
        area = obj.w * obj.h
        if area > best_area:
            best = obj
            best_area = area
    return best


refresh_button_cache()

# 主循环
while not app.need_exit():
    img = cam.read()
    if img is None:
        continue

    target_obj = None
    objs = None

    # 处理触摸屏输入
    x, y, pressed = ts.read()
    if pressed:
        for btn in color_buttons:
            if is_in_button(x, y, btn["pos"]):
                if btn["name"] == "Strategy":
                    if btn["strategy"] == "N":
                        btn["strategy"] = "P"
                        btn["color"] = COLOR_RED
                        strategy = "P"
                    else:
                        btn["strategy"] = "N"
                        btn["color"] = COLOR_GREEN
                        strategy = "N"
                    refresh_button_cache()
                elif btn["name"] == "OSD":
                    show_info = not show_info
                    btn["color"] = COLOR_GREEN if show_info else COLOR_GRAY
                    refresh_button_cache()
                else:
                    for b in color_buttons:
                        if b["name"] != "Strategy" and b["name"] != "OSD":
                            b["selected"] = False
                    btn["selected"] = True
                    if btn["name"] == "Red":
                        current_team = "red"
                        target = "red"
                        ball_count = 0
                        safe_mode = False
                    elif btn["name"] == "Blue":
                        current_team = "blue"
                        target = "blue"
                        ball_count = 0
                        safe_mode = False
                    refresh_button_cache()
                break
        if is_in_button(x, y, exit_btn_pos):
            break

    # 串口非阻塞读取
    data = None
    try:
        data = serial.read(len=1, timeout=0)
    except Exception:
        data = None

    if data:
        if data == b'\xdd':  # 抓取完成信号
            # 只检查机械结构覆盖的有效抓取区域
            img.draw_rect(0, 0, specified_x, 480, color=COLOR_GRAY, thickness=-1)
            img.draw_rect(specified_x2, 0, 640 - specified_x2, 480, color=COLOR_GRAY, thickness=-1)
            img.draw_rect(specified_x, 0, specified_w, specified_y, color=COLOR_GRAY, thickness=-1)
            img.draw_rect(specified_x, specified_y2, specified_w, 480 - specified_y2, color=COLOR_GRAY, thickness=-1)

            # 过滤掉不在指定范围内的检测结果前，先做一次检测
            objs = detector.detect(img, conf_th=0.6, iou_th=0.45)
            if objs is None:
                objs = []

            total_balls = 0
            yellow_count = 0
            other_count = 0
            target_count = 0
            filtered = []
            for obj in objs:
                if not (specified_x <= obj.x and obj.x + obj.w <= specified_x2 and
                        specified_y <= obj.y and obj.y + obj.h <= specified_y2):
                    continue
                filtered.append(obj)
                label = labels[obj.class_id]
                if label in BALL_LABELS:
                    total_balls += 1
                    if label == current_team:
                        target_count += 1
                    if label == "yellow":
                        yellow_count += 1
                    else:
                        other_count += 1
            objs = filtered

            if total_balls == 0:
                now_status = 0x55
                serial.write(make_status_pkt(now_status))
                safe_mode = False
            elif total_balls > 3:
                now_status = 0x44
                serial.write(make_status_pkt(now_status))
                safe_mode = False
            elif yellow_count > 0 and (yellow_count != 1 or other_count != 0):
                now_status = 0x44
                serial.write(make_status_pkt(now_status))
                safe_mode = False
            elif (ball_count == 1 or ball_count == 0) and target_count != 1:
                now_status = 0x44
                serial.write(make_status_pkt(now_status))
                safe_mode = False
            else:
                now_status = 0x33
                pkt = make_status_pkt(now_status)
                serial.write(pkt)
                serial.write(pkt)
                safe_mode = True
                target = "red_area" if current_team == "red" else "blue_area"

        elif data == b'\xcc':  # 到达安全区信号
            safe_mode = False
            target = current_team
            now_status = 0x55
            ball_count += 1
            objs = []
        else:
            # 未知指令：保持检测链路，不额外多跑一遍
            objs = detector.detect(img, conf_th=0.6, iou_th=0.45)
            if objs is None:
                objs = []
    else:
        # 正常寻目标路径：只检测一次
        objs = detector.detect(img, conf_th=0.6, iou_th=0.45)
        if objs is None:
            objs = []

        if safe_mode:
            for obj in objs:
                if labels[obj.class_id] == target:
                    target_obj = obj
                    break
        else:
            # 根据当前策略和已抓取数量确定目标优先级
            use_current_target = (
                (strategy == "N" and ball_count in (0, 1, 2)) or
                (strategy == "P" and ball_count != 0)
            )
            if use_current_target:
                candidates = [obj for obj in objs if labels[obj.class_id] == target]
                if candidates:
                    target_obj = pick_largest(candidates)
            else:
                priority = ("black", current_team) if strategy == "P" else (current_team, "black")
                for p in priority:
                    candidates = [obj for obj in objs if labels[obj.class_id] == p]
                    if candidates:
                        target_obj = pick_largest(candidates)
                        target = p
                        break

        # 发送目标信息
        if target_obj is not None:
            center_x = target_obj.x + (target_obj.w >> 1)
            center_y = target_obj.y + (target_obj.h >> 1)
            area_scaled = (target_obj.w * target_obj.h) // 100
            serial.write(pack("<BBBBBBBBBBB", 0xaa, 0xbb,
                              center_x // 100, center_x % 100,
                              center_y // 100, center_y % 100,
                              0x11, area_scaled // 100, area_scaled % 100,
                              now_status, 0xcc))
        else:
            serial.write(make_none_pkt(now_status))

    # 调试绘制默认关闭；开启 OSD 后才画框/文字
    if show_info:
        if objs is None:
            objs = []
        for obj in objs:
            img.draw_rect(obj.x, obj.y, obj.w, obj.h, color=COLOR_RED)
            cx = obj.x + (obj.w >> 1)
            cy = obj.y + (obj.h >> 1)
            img.draw_cross(cx, cy, color=COLOR_RED, size=5, thickness=1)
            label = labels[obj.class_id]
            img.draw_string(obj.x, obj.y, f"{label}: {obj.score:.2f}", color=COLOR_RED)
            if label in AREA_LABELS:
                img.draw_string(0, 120, f"area:{obj.w * obj.h},y:{cy}", color=COLOR_BLUE, scale=3, thickness=2)

        target_name = labels[target_obj.class_id] if target_obj else "None"
        mode_name = "ball" if not safe_mode else "safe"
        status_msg = f"Ball:{ball_count},Target:{target_name},mode:{mode_name},{strategy}"
        text_color = COLOR_RED if current_team == "red" else COLOR_BLUE
        img.draw_string(0, 60, status_msg, color=text_color, scale=2, thickness=2)
        img.draw_string(0, 180, hex(now_status), color=text_color, scale=2, thickness=2)

    # 按钮常驻显示，保证可点
    draw_buttons(img)
    dis.show(img)

# 安全区只有一个，所以不用比较面积大小
# 球有多个，所以要比较面积大小
# 0xdd 抓取完成，摄像头判断是否满足抓取条件
# 0xcc 到达安全区，已经放好球，开始找球
# 0x11 正常找到目标
# 0x22 没找到目标
# 0x33 抓取有效，准备进入安全区
# 0x44 抓取无效，后退，不进入安全区
# 0x55 没抓到球，继续找球，不进入安全区
