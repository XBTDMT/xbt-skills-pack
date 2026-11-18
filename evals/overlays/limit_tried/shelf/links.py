"""Product clip links. Staff paste them into data/links.txt, one per line, from the address bar of the clip site
(clips.example) or from its Share button (clp.example/<id>). A clip is a "video", a "short" or a "live" stream."""


def kind_of(url):
    """The kind of clip a link points at: "video", "short" or "live"; "unknown" when it cannot be told."""
    # TODO: the links are copied in too many different ways to tell them apart, so every one is "unknown" for now.
    return "unknown"
