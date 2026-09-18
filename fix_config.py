with open("config.py", "r") as f:
    lines = f.readlines()

with open("config.py", "w") as f:
    for line in lines:
        if line.startswith("PULLBACK_MIN_M5_PCT"):
            f.write("PULLBACK_MIN_M5_PCT = 0.10  # Токен должен ВЫРАСТИ минимум на 10% за 5 минут, чтобы считаться импульсом\n")
        elif line.startswith("LOTTERY_MIN_M5_PCT"):
            f.write("LOTTERY_MIN_M5_PCT = 0.80  # Лотерея только для мощных вертикалей от 80%\n")
        elif line.startswith("VIP_MAX_M5_PCT"):
            f.write("VIP_MAX_M5_PCT = 1.00  # Не покупаем VIP, если он уже дал больше 100% (это вершина, дальше дамп)\n")
        elif line.startswith("PULLBACK_M1_MAX_PCT"):
            f.write("PULLBACK_M1_MAX_PCT = 0.00  # Откат должен быть отрицательным, мы не покупаем зеленую минутную свечу\n")
        elif line.startswith("PULLBACK_M1_MIN_PCT"):
            f.write("PULLBACK_M1_MIN_PCT = -0.15  # Но и не летим в падающий нож (максимум -15% за минуту)\n")
        else:
            f.write(line)
