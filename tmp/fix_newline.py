import io
path = r"D:\AI\hzstock\launcher\windows\HengzhiLauncher.ps1"
s = io.open(path, encoding="utf-8-sig").read()
bslash = chr(92)
old1 = bslash + "r" + bslash + "n"          # \r\n 字面 4 字符
new1 = "`" + "r`" + "n"                      # PowerShell 的 `r`n
count = s.count('"' + old1 + '"')
print("found literal:", count)
s = s.replace('"' + old1 + '"', '"' + new1 + '"')
io.open(path, "w", encoding="utf-8-sig").write(s)
print("replaced", count)
