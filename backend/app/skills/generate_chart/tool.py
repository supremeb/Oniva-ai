"""
Generate Chart Skill
Generate charts and visualizations from data.
"""

import base64
import io
from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def generate_chart(
    ctx: SkillContext,
    data: Dict[str, Any],
    chart_type: str = "bar",
    title: str = "Chart",
) -> Dict[str, Any]:
    """
    Generate a chart from data.

    Args:
        ctx: Skill execution context
        data: Data to visualize (dict with 'labels' and 'values')
        chart_type: Type of chart ('bar', 'line', 'pie')
        title: Chart title

    Returns:
        Base64-encoded chart image
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt

        labels = data.get("labels", [])
        values = data.get("values", [])

        if not labels or not values:
            return {"error": "Data must contain 'labels' and 'values' arrays"}

        fig, ax = plt.subplots(figsize=(10, 6))

        if chart_type == "bar":
            ax.bar(labels, values)
        elif chart_type == "line":
            ax.plot(labels, values, marker='o')
        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct='%1.1f%%')
        else:
            return {"error": f"Unsupported chart type: {chart_type}"}

        ax.set_title(title)

        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)

        return {
            "success": True,
            "chart_type": chart_type,
            "image_base64": image_base64,
            "image_url": f"data:image/png;base64,{image_base64}",
        }
    except ImportError:
        return {
            "success": False,
            "error": "matplotlib not available",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }
