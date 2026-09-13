import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''        except Exception as e:
            print(f"Ошибка в менеджере позиций: {e}")
        await asyncio.sleep(0.5) # МИКРОСЕКУНДНЫЙ ТРЕКИНГ: Проверяем стопы каждые 0.5 сек для мгновенных экзитов! вместо 10, чтобы избежать сильных проскальзываний на дампах!'''

new_logic = '''        except Exception as e:
            print(f"Ошибка в менеджере позиций: {e}")
        # GeckoTerminal разрешает максимум 30 запросов в минуту. 
        # Если делать sleep(0.5), будет 120 запросов, что вызовет жесткий бан и ослепит бота!
        # Ставим интервал 3 секунды (20 запросов в минуту) - это максимально быстро и безопасно.
        await asyncio.sleep(3.0)'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
