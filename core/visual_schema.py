from typing import List, Optional, Literal
from dataclasses import dataclass, field

# kw_only so the subclasses can add required fields (data, chart_type) after
# the base class's defaulted ones.


@dataclass(kw_only=True)
class TableData:
    headers: List[str]
    rows: List[List[str]]


@dataclass(kw_only=True)
class ChartDataset:
    label: str
    values: List[float]


@dataclass(kw_only=True)
class ChartData:
    labels: List[str]
    datasets: List[ChartDataset]


@dataclass(kw_only=True)
class Visual:
    type: Literal["table", "chart"]
    title: str
    placement: Literal["after_paragraph", "end_of_section", "start_of_section"] = "after_paragraph"
    size: Literal["small", "medium", "large"] = "medium"
    caption: Optional[str] = None
    insert_after_paragraph: Optional[int] = None  # 0-indexed


@dataclass(kw_only=True)
class Table(Visual):
    data: TableData
    type: Literal["table"] = "table"


@dataclass(kw_only=True)
class Chart(Visual):
    chart_type: Literal["line", "bar", "pie", "area"]
    data: ChartData
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    type: Literal["chart"] = "chart"


@dataclass(kw_only=True)
class Section:
    title: str
    content: str
    visuals: List[Visual] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "visuals": self.visuals,
        }
