"""Duration strings for logs."""

def format_elapsed(s):
    return f"{s:.2f}s" if s < 60 else f"{int(s//60)}m {s%60:.1f}s"
