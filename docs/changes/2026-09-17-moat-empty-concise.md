# 2026-09-17 护城河章节：全维度资料不足时收敛为一句话

## 性质与范围

公司研究页 07 竞争优势章节此前在零证据时仍逐条列出 12+ 个维度、每条都写
"资料不足，暂无法判断"，形成一整面空条目墙，老板无法从中获得任何信息。

本次改为：**当没有任何维度获得较明确证据支持（SUPPORTED）时，整节只输出两句话**——
证据/反证计数 + 一条纪律性说明，不再逐维度展开。只要有一个维度有支持证据，
仍保留完整的逐维度展开（含部分证据、反证的权衡提示）。

改了两层渲染（同一段逻辑此前存在两份）：

- `agent/src/cio_report/builder.py` `build_moat`：章节落库时的 narrative_md
  （公司研究页逐节展示读这一份）。
- `agent/src/cio_report/narrative.py` `BossRenderer.section_moat`：飞书整卡
  老板叙述层的实时渲染。

同步 bump 版本门驱动存量报告自动重建：

- `SECTION_TEMPLATE_VERSION` v2 → v3（builder.py，章节指纹变化 → 逐节 REFRESHED）
- `NARRATIVE_TEMPLATE_VERSION` boss-narrative-v4 → v5（service.py，整卡指纹变化 → 重新综合）

简洁版文案保留了测试钉死的两句纪律性表述（"不据此认定竞争优势"、"规模、排名或
知名度本身不构成护城河"），未放松"无证据不给结论"的研究纪律。

**未动**：护城河研究服务本身（`moat_research`）、维度清单、SUPPORTED/PARTIAL 的
判定逻辑、反证提示、测试钉死的纪律文案。

## 根因

`build_moat` / `section_moat` 都把"逐维度展开"放在"是否有支持证据"判断之前，
导致全空情形也先渲染完整列表，只在末尾追加一句兜底说明——列表本身没有被
条件收敛。

## 验证方式与结果

- `pytest agent/tests/test_cio_narrative.py test_cio_report.py
  test_moat_thesis_integration.py test_moat_research.py` → 36 passed；
  CIO 全家族 9 个测试文件 → 79 passed。
- 模拟 12 个维度全部 UNKNOWN 的真实渲染：两层均输出两句话
  （"证据 0 条、反证 0 条。/ 各维度研究资料尚不完整，暂无法判断，不据此认定
  竞争优势；规模、排名或知名度本身不构成护城河。"），无英文枚举残留。
- 有 SUPPORTED 维度的情形仍走完整展开分支（逻辑分支未删，仅前移判断）。
- `ruff check` 仅报 narrative.py:96 E402，为 HEAD 上已有的规避循环导入写法，
  非本次引入，不处理。

## 上线条件

随下次 16:45 EOD 链自动生效：版本门 bump 后全部报告指纹变化，链内重建会以新
文案重写 07 章节。无需重启 serve（版本门是读代码常量，EOD 进程内新逻辑同样生效——
注意：**若 serve 进程仍运行旧代码，需重启后当晚 EOD 才会带新逻辑**）。

## 观察点

- 明晚报告抽查任一重点公司 07 章节，应为两句话而非空条目墙。
- 若后续护城河证据管线（年报/经营资料采集）接通，某公司出现第一个 SUPPORTED
  维度时，该节应自动恢复逐维度展开。

## 不要动清单的延续

- `test_moat_stays_disciplined_no_free_moat` 钉死的两句纪律表述必须保留在任何
  未来文案改版里。
- 无证据不认定竞争优势的研究纪律（no free moat）不得因简化展示而放松。
