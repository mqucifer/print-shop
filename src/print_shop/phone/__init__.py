"""The phone's Start, on the home Wi-Fi only (mqucifer/print-shop#214).

A phone paired at the Mac (`phones.py`) opens the Start page (`server.py`). It shows the job
that's ready, what Start checks, and the bed's question. Its confirm (`confirm.py`) is bound to
that phone, that job, the gate's word for a press, the G-code's hash and the phone's address,
works once, and lasts `confirm.CONFIRM_LASTS_S`. Only from a phone's own address on the home
network (`home.py`) does it go to the gate, which arms that one start once the checks at Start
pass (`arming.OneStart`).

Nothing here arms by itself, and nothing here builds a printer client.
"""
