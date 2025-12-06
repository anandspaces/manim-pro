from manim import *
import numpy as np

class MinimalSurfaceScene(ThreeDScene):
    def construct(self):
        # Camera setup
        self.set_camera_orientation(phi=60*DEGREES, theta=30*DEGREES)

        # Enneper minimal surface
        func = lambda u, v: np.array([
            u - (u**3)/3 + u*(v**2),
            v - (v**3)/3 + v*(u**2),
            u**2 - v**2
        ])

        surface = Surface(
            func,
            u_range=[-1.4, 1.4],
            v_range=[-1.4, 1.4],
            resolution=(40, 40),
            fill_opacity=0.9,
        )

        surface.set_color_by_gradient(BLUE, PURPLE, TEAL)

        axes = ThreeDAxes()

        self.add(axes)
        self.play(Create(surface), run_time=3)

        self.begin_ambient_camera_rotation(rate=0.2)
        self.wait(6)
        self.stop_ambient_camera_rotation()

        self.play(FadeOut(surface), FadeOut(axes))
