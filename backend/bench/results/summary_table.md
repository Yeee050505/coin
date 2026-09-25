| 模式 | 三次耗时(s) | 平均(s) | 中文字数(三次) | 平均 | 成功率 | 兜底 | OOM事件 |
|---|---|---|---|---|---|---|---|
| deepseek | 120.6/96.5/96.5 | 104.5 | 3889/3736/4011 | 3879 | 3/3 | 0 | 0 |
| auto | 224.9/160.6/176.7 | 187.4 | 4666/3786/4143 | 4198 | 3/3 | 0 | 0 |
| local_qwen | 329.5/409.9/409.8 | 383.1 | 3452/4679/4373 | 4168 | 3/3 | 3 | 3 |

router(auto): {'local_qwen': 36, 'deepseek': 15}
mode stats: {"deepseek": {"fallback": 0, "router": {}, "oom_or_crash": 0, "errors": 0}, "auto": {"fallback": 0, "router": {"local_qwen": 36, "deepseek": 15}, "oom_or_crash": 0, "errors": 0}, "local_qwen": {"fallback": 3, "router": {}, "oom_or_crash": 15, "errors": 0}}
commit: 448d507
