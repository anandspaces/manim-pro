import logging
from typing import Optional
import google.generativeai as genai

from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL
)

from src.utilities import sanitize_class_name

logger = logging.getLogger("manim_ai_server")

def validate_and_fix_script(script: str) -> str:
    """
    Validate and auto-fix common Manim script errors.
    Returns fixed script or raises ValueError if unfixable.
    """
    issues_found = []
    
    # Check 1: Look for np.random.randn patterns
    if 'np.random.randn(2)' in script:
        issues_found.append("Found np.random.randn(2) - needs 3D coordinates")
        # Try to auto-fix by replacing with randn(3) and limiting last dimension
        script = script.replace('np.random.randn(2)', 'np.array([np.random.uniform(-1, 1), np.random.uniform(-1, 1), 0])')
        logger.info("Auto-fixed: Replaced np.random.randn(2) with 3D array")
    
    # Check 2: Look for common 2D position errors
    patterns_to_fix = [
        ('np.random.randn(2)', 'np.array([np.random.uniform(-0.5, 0.5), np.random.uniform(-0.5, 0.5), 0])'),
        ('+ 0.3*np.random', '+ np.array([0.3, 0.2, 0])'),
        ('+ 0.5*np.random', '+ np.array([0.5, 0.3, 0])'),
        ('shift(0.05*np.random', 'shift(np.array([0.05, 0.03, 0])'),
        ('shift(0.2*np.random', 'shift(np.array([0.2, 0.15, 0])'),
        ('shift(0.8*np.random', 'shift(np.array([0.8, 0.6, 0])'),
    ]
    
    for pattern, replacement in patterns_to_fix:
        if pattern in script:
            script = script.replace(pattern, replacement)
            logger.info(f"Auto-fixed: Replaced '{pattern}' with '{replacement}'")
    
    # Check 3: Ensure numpy import if using np
    if 'np.' in script and 'import numpy as np' not in script:
        # Add import after the manim import
        script = script.replace('from manim import *', 'from manim import *\nimport numpy as np')
        logger.info("Auto-fixed: Added numpy import")
    
    # Check 4: Look for excessive object creation
    if script.count('for _ in range(') > 2:
        logger.warning("Script contains multiple loops - may create too many objects")
        issues_found.append("Multiple for loops detected - may impact performance")
    
    # Check 5: Validate basic structure
    if 'class ' not in script or 'Scene):' not in script:
        raise ValueError("Script missing class definition or Scene inheritance")
    
    if 'def construct(self):' not in script:
        raise ValueError("Script missing construct method")
    
    if issues_found:
        logger.warning(f"Script validation found issues: {', '.join(issues_found)}")
    
    return script


def auto_fix_script(script: str) -> str:
    rate_func_fixes = {
        'ease_in_out_sine': 'smooth',
        'ease_in_sine': 'slow_into',
        'ease_out_sine': 'rush_from',
        'ease_in_out': 'smooth',
        'ease_in': 'rush_into',
        'ease_out': 'rush_from',
        'ease_in_out_quad': 'smooth',
        'ease_in_out_cubic': 'smooth',
    }
    for old, new in rate_func_fixes.items():
        if old in script:
            logger.info(f"Auto-fixing: Replacing '{old}' with '{new}'")
            script = script.replace(old, new)
    return script



def generate_script_with_gemini(
    topic: str,
    subject: str, 
    chapter: str,
    level: int,
    audio_file_path: Optional[str] = None,
    audio_duration: Optional[float] = None
) -> str:
    """
    Generate context-aware Manim animation script using Gemini API.
    
    Args:
        topic: Specific topic to animate
        subject: Subject area (e.g., "Science", "Mathematics")
        chapter: Chapter context
        level: Grade level (1-12+)
        audio_file_path: Path to narration audio file
        audio_duration: Duration of audio in seconds
    """
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured in environment variables")
            
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        class_name = sanitize_class_name(topic)
        complexity = get_animation_complexity(level)
        
        # Base prompt with educational context
        base_prompt = f"""You are an expert educational animator creating a Manim visualization.

EDUCATIONAL CONTEXT:
- Subject: {subject}
- Chapter: {chapter}
- Topic: {topic}
- Grade Level: {level}
- Audience: Students in grade {level}

MISSION: Create a visually rich, educationally sound animation that helps students understand {topic} in the context of {chapter} ({subject}).

{complexity}

CRITICAL PERFORMANCE RULES:
1. **Use Scene class only** (NOT ThreeDScene - 3D is too slow)
2. **Simple 2D shapes**: Circle, Square, Rectangle, Triangle, Line, Arrow, Text, Dot
3. **Maximum object count based on level**:
   - Grades 1-5: Max 6 objects
   - Grades 6-8: Max 10 objects  
   - Grades 9-10: Max 12 objects
   - Grades 11-12+: Max 15 objects
4. **NO physics simulations** (no add_updater, particles, or per-frame calculations)
5. **Efficient animations**: Create, Write, FadeIn, FadeOut, Transform, ReplacementTransform
"""
        
        # Add audio integration
        if audio_file_path and audio_duration:
            audio_section = f"""
AUDIO INTEGRATION (CRITICAL):
- Audio file: {audio_file_path}
- Duration: {audio_duration:.2f} seconds
- **FIRST line in construct() MUST be**: self.add_sound("{audio_file_path}")
- **Total animation MUST match audio duration**: {audio_duration:.2f}s
- Sync visual changes with narration pacing
- Use self.wait() for natural pauses

TIMING BREAKDOWN for {audio_duration:.1f}s:
- Introduction (title/hook): 15-20% of duration ({audio_duration * 0.15:.1f}-{audio_duration * 0.20:.1f}s)
- Core content (main animation): 60-70% ({audio_duration * 0.60:.1f}-{audio_duration * 0.70:.1f}s)
- Conclusion/summary: 10-15% ({audio_duration * 0.10:.1f}-{audio_duration * 0.15:.1f}s)
"""
            duration_target = audio_duration
        else:
            audio_section = "# No audio provided - create 10 second animation"
            duration_target = 10
        
        # Content guidance based on subject
        subject_guidance = ""
        if "science" in subject.lower() or "physics" in subject.lower():
            subject_guidance = """
SCIENCE VISUALIZATION:
- Show processes, cycles, or systems
- Use arrows to indicate force, flow, or direction  
- Color-code different components
- Demonstrate cause and effect relationships
- Include labels for key parts
"""
        elif "math" in subject.lower():
            subject_guidance = """
MATHEMATICS VISUALIZATION:
- Show geometric relationships visually
- Use color to distinguish different elements
- Animate transformations step-by-step
- Display equations as Text (spell out if complex)
- Demonstrate concepts with concrete shapes
"""
        elif "biology" in subject.lower():
            subject_guidance = """
BIOLOGY VISUALIZATION:
- Represent biological structures with simple shapes
- Use color to show different cell types, organisms, etc.
- Animate processes like cell division, energy flow
- Label important structures
- Show scale and organization
"""
        
        prompt = base_prompt + audio_section + subject_guidance + f"""
CONTENT REQUIREMENTS:
1. **Educational accuracy**: Content must be scientifically/mathematically correct
2. **Progressive complexity**: Build from simple to complex
3. **Visual storytelling**: Each animation should add to understanding
4. **Clear labels**: Use Text objects to label key concepts (appropriate font size for grade {level})
5. **Color meaning**: Use color purposefully to distinguish concepts
6. **Smooth flow**: Transitions should feel natural and help understanding

CRITICAL CODING RULES:
1. **ALL positions must use NumPy arrays for arithmetic**:
   ✓ CORRECT: pos_top = np.array([0, 2.0, 0])
   ✗ WRONG:   pos_top = [0, 2.0, 0]  # This breaks when doing pos_top + [x, y, z]

2. **When doing position math, ALWAYS wrap in np.array()**:
   ✓ CORRECT: Arrow(start=pos_top + np.array([0.8, -0.5, 0]), end=pos_right)
   ✗ WRONG:   Arrow(start=pos_top + [0.8, -0.5, 0], end=pos_right)  # This concatenates!

3. **Alternative: Use explicit coordinates**:
   ✓ CORRECT: Arrow(start=[0.8, 1.5, 0], end=[3.0, 0.8, 0])

4. **Avoid runtime calculations**: Pre-calculate all positions
5. **Test before return**: Ensure all animations are valid

WHY THIS MATTERS:
Python list addition concatenates:     [0, 2, 0] + [0.8, -0.5, 0] = [0, 2, 0, 0.8, -0.5, 0]  ❌ 6 elements
NumPy array addition does math: np.array([0, 2, 0]) + np.array([0.8, -0.5, 0]) = [0.8, 1.5, 0]  ✓ 3 elements

TEMPLATE STRUCTURE:
```python
from manim import *
import numpy as np

class {class_name}(Scene):
    def construct(self):
        # 1. AUDIO INTEGRATION
        {'self.add_sound("' + audio_file_path + '")' if audio_file_path else '# No audio'}
        
        # 2. DEFINE POSITIONS AS NUMPY ARRAYS (CRITICAL!)
        pos_top = np.array([0, 2.0, 0])
        pos_right = np.array([3.5, 0, 0])
        pos_bottom = np.array([0, -2.0, 0])
        pos_left = np.array([-3.5, 0, 0])
        
        # 3. TITLE/INTRODUCTION (3-5 seconds)
        title = Text("{topic}", font_size=APPROPRIATE_SIZE)
        subtitle = Text("{chapter}", font_size=SMALLER_SIZE).next_to(title, DOWN)
        
        self.play(Write(title), run_time=1.5)
        self.play(FadeIn(subtitle), run_time=0.8)
        self.wait(0.5)
        
        # Move title to make room for content
        self.play(
            title.animate.scale(0.5).to_edge(UP),
            FadeOut(subtitle),
            run_time=1
        )
        
        # 4. MAIN EDUCATIONAL CONTENT
        
        # Create objects at base positions
        circle_top = Circle(radius=0.6, color=BLUE).move_to(pos_top)
        circle_right = Circle(radius=0.6, color=GREEN).move_to(pos_right)
        
        # Connect with arrows - USE NUMPY ARRAY ARITHMETIC
        arrow_1 = Arrow(
            start=pos_top + np.array([0.8, -0.5, 0]),     # ✓ CORRECT
            end=pos_right + np.array([-0.5, 0.8, 0]),     # ✓ CORRECT
            color=WHITE,
            buff=0.1
        )
        
        # OR use explicit coordinates (also safe)
        arrow_2 = Arrow(
            start=[3.5, -0.5, 0],   # Explicit coordinates
            end=[0, -1.5, 0],
            color=WHITE,
            buff=0.1
        )
        
        self.play(Create(circle_top), Create(circle_right))
        self.play(Create(arrow_1), Create(arrow_2))
        
        # 5. CONCLUSION (2-3 seconds)
        self.wait(0.5)
```

EXAMPLE - Kinetic Theory (Grade 10 Science):
```python
from manim import *
import numpy as np

class KineticTheoryScene(Scene):
    def construct(self):
        # Audio integration
        self.add_sound("/path/to/audio.wav")
        
        # Title
        title = Text("Kinetic Theory of Matter", font_size=48)
        subtitle = Text("Matter & Energy", font_size=36).next_to(title, DOWN)
        
        self.play(Write(title), run_time=1.5)
        self.play(FadeIn(subtitle), run_time=0.8)
        self.wait(0.5)
        
        self.play(
            title.animate.scale(0.5).to_edge(UP),
            FadeOut(subtitle),
            run_time=1
        )
        
        # Section 1: States of matter (8s)
        solid = Circle(radius=1, color=BLUE).shift(LEFT * 3.5)
        liquid = Circle(radius=1, color=GREEN)
        gas = Circle(radius=1, color=RED).shift(RIGHT * 3.5)
        
        solid_label = Text("Solid", font_size=36).next_to(solid, DOWN)
        liquid_label = Text("Liquid", font_size=36).next_to(liquid, DOWN)
        gas_label = Text("Gas", font_size=36).next_to(gas, DOWN)
        
        self.play(
            Create(solid), Create(liquid), Create(gas),
            Write(solid_label), Write(liquid_label), Write(gas_label),
            run_time=2
        )
        
        # Section 2: Particle arrangement (10s)
        # Fixed positions for solid particles (tight arrangement)
        solid_positions = [
            [-3.5, 0.3, 0], [-3.3, 0.3, 0], [-3.7, 0.3, 0],
            [-3.5, 0, 0], [-3.3, 0, 0], [-3.7, 0, 0],
            [-3.5, -0.3, 0], [-3.3, -0.3, 0], [-3.7, -0.3, 0]
        ]
        solid_particles = VGroup(*[Dot(pos, radius=0.08, color=BLUE_A) for pos in solid_positions])
        
        # Liquid particles (looser)
        liquid_positions = [
            [0, 0.4, 0], [0.3, 0.2, 0], [-0.3, 0.3, 0],
            [0, 0, 0], [0.35, -0.1, 0], [-0.35, 0, 0],
            [0, -0.4, 0], [0.3, -0.3, 0], [-0.3, -0.35, 0]
        ]
        liquid_particles = VGroup(*[Dot(pos, radius=0.08, color=GREEN_A) for pos in liquid_positions])
        
        # Gas particles (spread out)
        gas_positions = [
            [3.5, 0.6, 0], [3.8, 0.3, 0], [3.2, 0.4, 0],
            [3.5, 0, 0], [3.9, -0.2, 0], [3.1, 0.1, 0],
            [3.5, -0.5, 0], [3.7, -0.6, 0], [3.3, -0.4, 0]
        ]
        gas_particles = VGroup(*[Dot(pos, radius=0.08, color=RED_A) for pos in gas_positions])
        
        self.play(
            FadeIn(solid_particles),
            FadeIn(liquid_particles),
            FadeIn(gas_particles),
            run_time=1.5
        )
        self.wait(1)
        
        # Section 3: Show motion (12s)
        motion_text = Text("Particles in constant motion", font_size=40).to_edge(DOWN)
        self.play(Write(motion_text), run_time=1.5)
        
        # Animate different motion levels (fixed positions, not random)
        self.play(
            solid_particles[0].animate.shift([0.05, 0.05, 0]),
            solid_particles[2].animate.shift([-0.05, 0.03, 0]),
            run_time=0.8
        )
        
        self.play(
            liquid_particles[1].animate.shift([0.15, -0.1, 0]),
            liquid_particles[4].animate.shift([-0.2, 0.15, 0]),
            liquid_particles[7].animate.shift([0.1, 0.2, 0]),
            run_time=1.2
        )
        
        self.play(
            gas_particles[0].animate.shift([0.3, -0.4, 0]),
            gas_particles[3].animate.shift([-0.4, 0.35, 0]),
            gas_particles[6].animate.shift([0.35, 0.3, 0]),
            gas_particles[8].animate.shift([-0.3, -0.3, 0]),
            run_time=1.5
        )
        self.wait(1)
        
        # Section 4: Temperature relationship (8s)
        temp_text = Text("Higher Temperature = Faster Motion", font_size=40)
        arrow = Arrow(start=LEFT*2, end=RIGHT*2, color=YELLOW)
        temp_group = VGroup(temp_text, arrow).arrange(DOWN).to_edge(DOWN)
        
        self.play(
            Transform(motion_text, temp_text),
            Create(arrow),
            run_time=2
        )
        
        # Show gas particles moving faster (color change)
        self.play(
            gas_particles.animate.set_color(ORANGE),
            gas_particles[1].animate.shift([0.4, 0.4, 0]),
            gas_particles[5].animate.shift([-0.4, -0.35, 0]),
            run_time=2
        )
        self.wait(1)
        
        # Conclusion (3s)
        self.play(
            FadeOut(solid), FadeOut(liquid), FadeOut(gas),
            FadeOut(solid_label), FadeOut(liquid_label), FadeOut(gas_label),
            FadeOut(solid_particles), FadeOut(liquid_particles), FadeOut(gas_particles),
            FadeOut(motion_text), FadeOut(arrow),
            run_time=2
        )
        
        summary = Text("Matter = Particles in Motion", font_size=48)
        self.play(Write(summary), run_time=1.5)
        self.wait(1)
        self.play(FadeOut(summary), run_time=1)
        
        self.wait(0.5)
```

This example shows:
✓ Fixed 3D positions [x, y, 0]
✓ Pre-defined particle arrays
✓ Controlled animations with specific shifts
✓ No random number generation
✓ Clear educational progression
✓ Proper timing for audio sync

SYNTAX REQUIREMENTS:
- Class name: {class_name}
- Import: from manim import *
- Optional: import numpy as np (only if needed for fixed arrays)
- Rate functions: smooth, linear, rush_into, rush_from, there_and_back
- NO markdown, NO explanations outside code
- Return ONLY executable Python code
- ALL positions must be 3D: [x, y, 0] or use UP/DOWN/LEFT/RIGHT
- NO np.random - use fixed positions only
- Keep performance optimized

QUALITY CHECKLIST:
✓ Educationally accurate content
✓ Appropriate complexity for grade {level}
✓ Visual elements support learning
✓ Clear, readable labels
✓ Smooth, purposeful animations
✓ Timing matches audio narration
✓ Total duration = {duration_target:.1f}s

REMEMBER: 
- ALWAYS use np.array([x, y, 0]) when defining positions that will be used in arithmetic
- ALWAYS wrap offsets in np.array() when adding to positions
- Test: pos + np.array([dx, dy, 0]) ✓  vs  pos + [dx, dy, 0] ✗

Generate the complete, elaborate Manim script now:
"""

        response = model.generate_content(prompt)
        script = response.text.strip()
        
        # Clean markdown
        if script.startswith("```python"):
            script = script.replace("```python", "").replace("```", "").strip()
        elif script.startswith("```"):
            script = script.replace("```", "").strip()
        
        # Auto-fix common errors
        script = auto_fix_script(script)
        
        # Validate and fix positioning errors
        script = validate_and_fix_script(script)
        
        # Verify audio integration
        if audio_file_path and "add_sound" not in script:
            logger.warning("Generated script missing audio integration, adding it...")
            lines = script.split('\n')
            for i, line in enumerate(lines):
                if 'def construct(self):' in line:
                    indent = ' ' * 8
                    lines.insert(i + 1, f'{indent}# Add narration audio')
                    lines.insert(i + 2, f'{indent}self.add_sound("{audio_file_path}")')
                    lines.insert(i + 3, '')
                    break
            script = '\n'.join(lines)
        
        logger.info(f"Generated {len(script)} char script for {topic} (Level {level})")
        return script
        
    except Exception as e:
        logger.error(f"Script generation error: {e}")
        raise Exception(f"Failed to generate script: {str(e)}")


def get_animation_complexity(level: int) -> str:
    """Get animation complexity guidance based on grade level"""
    if level <= 5:
        return """
ANIMATION STYLE FOR ELEMENTARY (Grades 1-5):
- Large, colorful shapes and simple icons
- Smooth, slow transitions (1.5-2s per animation)
- Maximum 5-6 visual elements total
- Use bright, friendly colors (BLUE, YELLOW, GREEN, RED, ORANGE)
- Simple movements: grow, shrink, slide, rotate
- Clear visual hierarchy with large text (font_size=60-72)
"""
    elif level <= 8:
        return """
ANIMATION STYLE FOR MIDDLE SCHOOL (Grades 6-8):
- Mix of shapes, diagrams, and annotated illustrations
- Moderate pace transitions (1-1.5s per animation)
- Up to 8-10 visual elements
- Professional but engaging colors
- Introduce basic diagrams and labeled components
- Text size: 48-60
- Show step-by-step processes
"""
    elif level <= 10:
        return """
ANIMATION STYLE FOR HIGH SCHOOL (Grades 9-10):
- Detailed diagrams, graphs, and visual models
- Faster, more efficient transitions (0.8-1.2s)
- Up to 10-12 elements
- Academic color schemes with good contrast
- Include arrows, labels, and annotations
- Text size: 40-48
- Demonstrate relationships and processes
- Can include simple formulas as text
"""
    else:
        return """
ANIMATION STYLE FOR ADVANCED (Grades 11-12+):
- Complex visualizations, detailed graphs, models
- Efficient transitions (0.6-1s)
- Up to 15 elements if needed
- Professional, scholarly presentation
- Sophisticated diagrams with multiple layers
- Text size: 36-44
- Show abstract concepts visually
- Include mathematical representations as text
- Demonstrate advanced relationships
"""

