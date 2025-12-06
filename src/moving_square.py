from manim import *

class MovingSquare(Scene):
    def construct(self):
        square = Square()
        square.set_fill(BLUE, opacity=0.7)

        self.play(Create(square))
        self.play(square.animate.shift(RIGHT * 2))
        self.wait(1)
