from typing import List, Optional, Literal
from dataclasses import dataclass, field

# kw_only=True is needed because subclasses add required fields (data,
# chart_type) after the base class's defaulted ones — without it Python
# raises "non-default argument follows default argument" on import.

@dataclass(kw_only=True)
class TableData:
    """Schema for table data"""
    headers: List[str]
    rows: List[List[str]]

@dataclass(kw_only=True)
class ChartDataset:
    """Schema for chart dataset"""
    label: str
    values: List[float]

@dataclass(kw_only=True)
class ChartData:
    """Schema for chart data"""
    labels: List[str]
    datasets: List[ChartDataset]

@dataclass(kw_only=True)
class Visual:
    """Base visual element"""
    type: Literal["table", "chart"]
    title: str
    placement: Literal["after_paragraph", "end_of_section", "start_of_section"] = "after_paragraph"
    size: Literal["small", "medium", "large"] = "medium"
    caption: Optional[str] = None
    insert_after_paragraph: Optional[int] = None  # 0-indexed paragraph number

@dataclass(kw_only=True)
class Table(Visual):
    """Table visual element"""
    data: TableData
    type: Literal["table"] = "table"

@dataclass(kw_only=True)
class Chart(Visual):
    """Chart visual element"""
    chart_type: Literal["line", "bar", "pie", "area"]
    data: ChartData
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    type: Literal["chart"] = "chart"

@dataclass(kw_only=True)
class Section:
    """Section with content and visuals"""
    title: str
    content: str
    visuals: List[Visual] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "visuals": self.visuals,
        }
