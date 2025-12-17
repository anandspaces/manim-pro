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



def clean_script_output(script: str) -> str:
    """
    Robustly clean LLM output to extract only Python code.
    Handles markdown fences, explanatory text, and other artifacts.
    """
    # Find the first Python import statement
    import_keywords = ['from manim import', 'import manim', 'import numpy']
    first_import_pos = len(script)
    
    for keyword in import_keywords:
        pos = script.find(keyword)
        if pos != -1 and pos < first_import_pos:
            first_import_pos = pos
    
    # If we found an import, start from there
    if first_import_pos < len(script):
        script = script[first_import_pos:]
    
    # Remove markdown code fences (anywhere in text)
    script = script.replace('```python', '')
    script = script.replace('```', '')
    
    # Remove any trailing explanatory text after the last class definition
    # Look for the last closing of the construct method
    lines = script.split('\n')
    last_meaningful_line = len(lines) - 1
    
    # Find last line that's part of the class (has indentation or is closing brace)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].rstrip()
        if line and (line.startswith('    ') or line.startswith('\t') or 'class ' in line):
            last_meaningful_line = i
            break
    
    script = '\n'.join(lines[:last_meaningful_line + 1])
    
    # Clean up whitespace
    script = script.strip()
    
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
        
        # Audio integration section
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
            audio_line = f'self.add_sound("{audio_file_path}")'
        else:
            audio_section = "# No audio provided - create 10 second animation"
            duration_target = 10
            audio_line = '# No audio'
        
        # Subject-specific guidance
        subject_guidance = get_subject_guidance(subject)
        
        # Main prompt
        prompt = f"""You are an expert educational animator creating a Manim visualization.

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

{audio_section}

{subject_guidance}

CRITICAL CODING RULES:
1. **ALL positions must use NumPy arrays for arithmetic**:
   ✓ CORRECT: pos_top = np.array([0, 2.0, 0])
   ✗ WRONG:   pos_top = [0, 2.0, 0]

2. **When doing position math, ALWAYS wrap in np.array()**:
   ✓ CORRECT: Arrow(start=pos_top + np.array([0.8, -0.5, 0]), end=pos_right)
   ✗ WRONG:   Arrow(start=pos_top + [0.8, -0.5, 0], end=pos_right)

3. **NO random number generation**: Use fixed positions only
4. **Test before return**: Ensure all animations are valid

TEMPLATE:
```
from manim import *
import numpy as np

class {class_name}(Scene):
    def construct(self):
        # 1. AUDIO INTEGRATION
        {audio_line}
        
        # 2. DEFINE POSITIONS AS NUMPY ARRAYS
        pos_center = np.array([0, 0, 0])
        pos_top = np.array([0, 2.0, 0])
        pos_bottom = np.array([0, -2.0, 0])
        
        # 3. TITLE/INTRODUCTION
        title = Text("{topic}", font_size=48)
        self.play(Write(title), run_time=1.5)
        self.play(title.animate.scale(0.6).to_edge(UP), run_time=1)
        
        # 4. MAIN CONTENT
        # [Your educational animation here]
        
        # 5. CONCLUSION
        self.wait(1)
```

CRITICAL OUTPUT FORMAT:
- Return ONLY the raw Python code
- NO markdown code fences (no ```python or ```)  
- NO explanatory text before or after the code
- NO comments outside the Python class
- Start your response directly with: from manim import *
- End your response with the last line of the construct method

QUALITY CHECKLIST:
✓ Educationally accurate content
✓ Appropriate complexity for grade {level}
✓ Clear, readable labels
✓ Smooth, purposeful animations
✓ Total duration = {duration_target:.1f}s
✓ NO markdown formatting in response

Generate the complete Manim script now. Remember: Output ONLY Python code, starting with 'from manim import *':"""

        response = model.generate_content(prompt)
        script = response.text.strip()
        
        # Robust cleanup
        script = clean_script_output(script)
        
        # Validate it starts correctly
        if not script.startswith('from manim import') and not script.startswith('import'):
            logger.error(f"Script doesn't start with import: {script[:100]}")
            raise ValueError("Generated script has invalid format")
        
        # Auto-fix common errors
        script = auto_fix_script(script)
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


def get_subject_guidance(subject: str) -> str:
    """Get subject-specific visualization guidance"""
    subject_lower = subject.lower()
    
    if "science" in subject_lower or "physics" in subject_lower:
        return """
SCIENCE VISUALIZATION:
- Show processes, cycles, or systems
- Use arrows to indicate force, flow, or direction  
- Color-code different components
- Demonstrate cause and effect relationships
- Include labels for key parts
"""
    elif "math" in subject_lower:
        return """
MATHEMATICS VISUALIZATION:
- Show geometric relationships visually
- Use color to distinguish different elements
- Animate transformations step-by-step
- Display equations as Text (spell out if complex)
- Demonstrate concepts with concrete shapes
"""
    elif "biology" in subject_lower:
        return """
BIOLOGY VISUALIZATION:
- Represent biological structures with simple shapes
- Use color to show different cell types, organisms, etc.
- Animate processes like cell division, energy flow
- Label important structures
- Show scale and organization
"""
    else:
        return """
GENERAL VISUALIZATION:
- Use clear visual metaphors
- Color-code related concepts
- Show relationships with arrows/lines
- Label all important elements
- Build understanding progressively
"""

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

