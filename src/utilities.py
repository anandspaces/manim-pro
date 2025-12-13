def sanitize_class_name(topic: str) -> str:
    words = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in topic).split()
    class_name = ''.join(word.capitalize() for word in words)
    if not class_name:
        class_name = "Animation"
    elif class_name[0].isdigit():
        class_name = "Anim" + class_name
    return class_name + "Scene"

def safe_filename(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid filename (contains path separators).")
    return name