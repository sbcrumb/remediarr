def build_coach_msg() -> str:
    """Build the coach message, using custom message if configured."""
    if cfg.MSG_COACH:
        return cfg.MSG_COACH
    
    # ...existing code (default coach message construction)...