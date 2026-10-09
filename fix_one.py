lines = open("analyzer.py").read().split("\n")
lines.insert(1440, "            except Exception as e:")
lines.insert(1441, "                pass")
open("analyzer.py", "w").write("\n".join(lines))
