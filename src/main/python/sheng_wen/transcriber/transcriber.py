from abc import ABC, abstractmethod
from typing import List, Dict, Any, Callable, Optional
from dataclasses import dataclass, field
import importlib

# --- 自定义异常 ---

class TranscriberError(Exception):
    """转录器模块的通用基础异常。"""
    pass

class ModelLoadError(TranscriberError):
    """在加载或初始化模型时发生错误的异常。"""
    pass

class TranscriptionError(TranscriberError):
    """在文件转录过程中发生错误的异常。"""
    pass

class TranscriptionCancelled(TranscriberError):
    """转录任务被外部取消（例如任务删除）。"""
    pass

# --- 数据类 ---

@dataclass
class TranscriptionResult:
    """
    一个用于保存转录结果的数据类，包含性能指标。
    """
    segments: List[Dict[str, Any]]  # 转录出的文本片段列表
    transcription_time: float       # 转录耗时（秒）
    real_time_factor: float         # 实时率 (RTF)，即处理时间 / 音频时长
    total_time: float               # 总耗时（秒），包括转录和其他开销
    model_load_time: float          # 模型加载耗时（秒）
    audio_duration: float           # 音频总时长（秒）
    language: str                   # 检测到的语言代码 (例如, "zh")
    language_probability: float     # 语言检测的置信度 (0-1)
    provider: str = "fast_whisper"
    artifacts: Dict[str, Any] = field(default_factory=dict)

# --- 抽象基类 ---

class Transcriber(ABC):
    """
    语音转文本转录器的抽象基类。
    """
    def __init__(self, **kwargs):
        """
        初始化转录器。
        
        参数:
            **kwargs: 实现类可能需要的任意关键字参数。
        """
        pass

    @abstractmethod
    def transcribe(
        self,
        file_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> TranscriptionResult:
        """
        转录一个音频或视频文件。

        参数:
            file_path: 媒体文件的路径。
            progress_callback: 一个可选的回调函数，接收一个 0.0 到 1.0 之间的浮点数表示进度。
            cancel_check: 一个可选回调，返回 True 表示应立即中止当前任务。

        返回:
            一个包含文本片段和性能指标的 TranscriptionResult 对象。
        """
        pass

# --- 工厂函数 ---

def get_transcriber(name: str, **kwargs) -> Transcriber:
    """
    一个工厂函数，用于获取指定名称的转录器实例。

    这允许我们在不直接依赖具体实现的情况下创建转录器。

    参数:
        name: 转录器的名称 (例如, "fast_whisper")。
        **kwargs: 传递给转录器构造函数的参数。

    返回:
        一个 Transcriber 的实例。
        
    异常:
        ValueError: 如果找不到指定名称的转录器模块。
        ModelLoadError: 如果在初始化模型时发生错误。
    """
    try:
        # 将 "fast_whisper" 转换为 "FastWhisperTranscriber"
        class_name = "".join(word.capitalize() for word in name.split('_')) + "Transcriber"
        # 动态构建模块路径
        module_name = f"src.main.python.sheng_wen.transcriber.{name}_transcriber"
        
        module = importlib.import_module(module_name)
        transcriber_class = getattr(module, class_name)
        
        # 在工厂函数中捕获模型加载错误
        return transcriber_class(**kwargs)
        
    except (ImportError, AttributeError) as e:
        raise ValueError(f"找不到名为 '{name}' 的转录器。请确保模块 '{module_name}' 和类 '{class_name}' 正确。") from e
    except TranscriberError: # 重新抛出我们自定义的异常
        raise

