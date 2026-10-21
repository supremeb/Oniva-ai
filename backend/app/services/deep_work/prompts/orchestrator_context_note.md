## Long runs

Old tool results may be cleared or saved to files to keep the conversation short. A cleared result says so and names the file it was saved to; read it with `read_file` (use `offset` and `limit`), or call the tool again.

If you see a summary of the earlier conversation, call `dw_get_learnings` and re-read the todo (`dw_save_learnings` and `dw_record_reflection` return it as `todo`) before continuing. Do not redo finished rounds.
