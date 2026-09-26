"""GideonAI Mail Desk runtime — a two-way conversational mail channel over stdlib IMAP/SMTP.

One naming note worth keeping: this package is deliberately NOT called ``email``. A
top-level module or package of that name would shadow the stdlib ``email`` package, and
every module in here leans on ``email.message`` / ``email.parser`` / ``email.utils``
resolving to the standard library. Because the app loader puts the app dir on
``sys.path`` at runtime, a sibling ``email.py`` would break MIME handling for this app
AND for anything else in the process that imports stdlib email.
"""
