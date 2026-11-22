# 电竞联赛赛制下的晋级偏差与补赛决策

组织方记录积分裁决及已经锁定的资格；先后手及替补记录并不构成额外正式积分。

records 保存可离线使用的合成业务记录。每行一个 UTF-8 JSON 对象，id 在各数据表内唯一，跨表键沿用业务记录中的标识。catalog.json 保存行数及原始文件校验值。

| 记录 | 业务含义 |
| --- | --- |
| teams.jsonl | seed 用于抽签限制，阵容数据用来分析已经开打的比赛。 |
| games.jsonl | slot 是赛季内相对排期。known_slot 是原排期已知的时点。 |
| slots.jsonl | 实际可补赛的场地和时间槽，以及不可被替换的活动。 |
| rules.jsonl | 同轮同场地只能打一局，休息按 slot 差值。tie_order 是正式判分口径。 |
| rulings.jsonl | 赛中裁决单由裁委处置；资格锁定的记录保留其当时来源。 |

## 核对资料

运行环境为 Python 3.11，使用标准库，无需下载依赖。用 `python3 -m unittest discover -s tests -v` 核对发行资料的完整性与约定的输入关系。
