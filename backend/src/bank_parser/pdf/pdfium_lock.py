"""
One process-wide lock for every call into PDFium (pypdfium2).

PDFium is not thread-safe: concurrent use from several threads -- e.g. the
browser requesting several page images at once while extraction workers
parse another document -- crashed the server process without a traceback
(DECISIONS.md D-018). Every pypdfium2 call in this package must hold this
lock. Calls are short (one page parse or render: milliseconds), so
serialising them costs little; separate *processes* (template learning)
have their own PDFium and are unaffected.
"""
import threading

PDFIUM_LOCK = threading.RLock()
