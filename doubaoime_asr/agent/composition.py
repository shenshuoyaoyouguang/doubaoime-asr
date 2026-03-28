from __future__ import annotations

from dataclasses import dataclass, field

from .input_injector import FocusTarget, WindowsTextInjector


@dataclass(slots=True)
class CompositionSession:
    injector: WindowsTextInjector
    target: FocusTarget
    rendered_text: str = field(default="", init=False)
    final_text: str = field(default="", init=False)

    def render_interim(self, text: str) -> str:
        self.injector.replace_text(self.target, self.rendered_text, text)
        self.rendered_text = text
        return text

    def finalize(self, text: str) -> str:
        self.injector.replace_text(self.target, self.rendered_text, text)
        self.rendered_text = text
        self.final_text = text
        return text
