"""
用户选择 FlyoutView 组件

用于在检测到多个微信账号时，让用户选择要使用的账号
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QButtonGroup, QWidget
from qfluentwidgets import FlyoutViewBase, BodyLabel, RadioButton, PrimaryPushButton, InfoBar, InfoBarPosition
import logging

logger = logging.getLogger("WeChatNotifier")


class UserSelectorFlyout(FlyoutViewBase):
    """用户选择 Flyout 视图"""
    
    # 用户选择完成信号，传递选中的用户路径
    userSelected = pyqtSignal(dict)
    # 用户取消选择信号
    userCancelled = pyqtSignal()
    
    def __init__(self, candidates: list, parent=None):
        """
        初始化用户选择器
        
        Args:
            candidates: 候选用户列表，每个元素为字典：
                       {'path': str, 'wxid': str, 'display': str, 'mtime': float}
            parent: 父窗口
        """
        super().__init__(parent)
        self.candidates = candidates
        self.selected_user = None
        
        # 主布局
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.setContentsMargins(20, 16, 20, 16)
        
        # 标题 - 使用强样式确保主题适配
        self.titleLabel = BodyLabel('检测到多个微信账号')
        self.titleLabel.setObjectName('titleLabel')
        self.titleLabel.setStyleSheet('''
            font-weight: bold; 
            font-size: 16px;
            color: rgba(0, 0, 0, 0.85);
        ''')
        self.vBoxLayout.addWidget(self.titleLabel)
        
        # 说明文字
        self.subtitleLabel = BodyLabel('请选择要使用的微信账号：')
        self.subtitleLabel.setObjectName('subtitleLabel')
        self.subtitleLabel.setStyleSheet('''
            font-size: 14px;
            color: rgba(0, 0, 0, 0.65);
        ''')
        self.vBoxLayout.addWidget(self.subtitleLabel)
        
        # 单选按钮组
        self.buttonGroup = QButtonGroup(self)
        self.radio_buttons = []
        
        # 为每个候选用户创建 RadioButton
        for i, candidate in enumerate(candidates):
            # 显示名称：优先使用 display，否则使用 wxid
            display_text = candidate.get('display', candidate['wxid'])
            rb = RadioButton(display_text)
            rb.setProperty('user_data', candidate)
            
            # 显示最后修改时间
            import datetime
            mtime_str = datetime.datetime.fromtimestamp(candidate['mtime']).strftime('%Y-%m-%d %H:%M:%S')
            rb.setStyleSheet('''
                margin-bottom: 10px; 
                font-size: 13px;
                color: rgba(0, 0, 0, 0.85);
                min-height: 35px;
            ''')
            
            self.vBoxLayout.addWidget(rb)
            self.buttonGroup.addButton(rb)
            self.radio_buttons.append(rb)
            
            # 默认选中第一个（最新的）
            if i == 0:
                rb.setChecked(True)
        
        # 按钮布局
        self.hBoxLayout = QHBoxLayout()
        self.hBoxLayout.setSpacing(10)
        
        # 确认按钮
        self.confirmButton = PrimaryPushButton('确认')
        self.confirmButton.setFixedWidth(100)
        self.confirmButton.clicked.connect(self.onConfirm)
        self.hBoxLayout.addWidget(self.confirmButton)
        
        # 取消按钮
        from qfluentwidgets import PushButton
        self.cancelButton = PushButton('取消')
        self.cancelButton.setFixedWidth(100)
        self.cancelButton.clicked.connect(self.onCancel)
        self.hBoxLayout.addWidget(self.cancelButton)
        
        self.vBoxLayout.addLayout(self.hBoxLayout)
        
        # 设置最小高度，确保所有 RadioButton 都能完整显示
        # 标题 (40px) + 副标题 (24px) + 间距 (12px) + 按钮组 (每个约 40px，包含间距) + 按钮布局 (50px) + 边距 (32px)
        min_height = 40 + 24 + 12 + (len(candidates) * 40) + 50 + 32
        self.setMinimumHeight(min(min_height, 600))  # 最多显示 600px 高度
        self.setFixedWidth(420)
        
        # 应用主题样式
        self._apply_theme()
    
    def _apply_theme(self):
        """应用当前主题颜色"""
        from qfluentwidgets import isDarkTheme, Theme
        
        # 检测当前主题
        dark = isDarkTheme()
        
        if dark:
            # 深色主题
            title_color = "rgba(255, 255, 255, 0.95)"
            subtitle_color = "rgba(255, 255, 255, 0.65)"
            text_color = "rgba(255, 255, 255, 0.85)"
            bg_color = "rgba(32, 32, 32, 0.95)"
        else:
            # 浅色主题
            title_color = "rgba(0, 0, 0, 0.85)"
            subtitle_color = "rgba(0, 0, 0, 0.65)"
            text_color = "rgba(0, 0, 0, 0.85)"
            bg_color = "rgba(255, 255, 255, 0.95)"
        
        # 应用颜色
        self.titleLabel.setStyleSheet(f'''
            font-weight: bold; 
            font-size: 16px;
            color: {title_color};
        ''')
        
        self.subtitleLabel.setStyleSheet(f'''
            font-size: 14px;
            color: {subtitle_color};
        ''')
        
        for rb in self.radio_buttons:
            rb.setStyleSheet(f'''
                margin-bottom: 10px; 
                font-size: 13px;
                color: {text_color};
                min-height: 35px;
            ''')
        
        # 设置背景色
        self.setStyleSheet(f'''
            UserSelectorFlyout {{
                background-color: {bg_color};
                border-radius: 8px;
            }}
        ''')
    
    def onConfirm(self):
        """确认选择"""
        # 获取选中的按钮
        checked_button = self.buttonGroup.checkedButton()
        if checked_button:
            user_data = checked_button.property('user_data')
            if user_data:
                self.selected_user = user_data
                logger.info(f"用户选择确认：{user_data['wxid']}")
                self.userSelected.emit(user_data)
        
        self.close()
    
    def onCancel(self):
        """取消选择"""
        logger.info("用户取消选择")
        self.userCancelled.emit()
        self.close()
    
    def get_selected_user(self) -> dict:
        """获取选中的用户数据"""
        return self.selected_user


def show_user_selector(candidates: list, target_widget, parent, on_selected=None, on_cancelled=None):
    """
    显示用户选择器
    
    Args:
        candidates: 候选用户列表
        target_widget: 目标组件（Flyout 相对于此组件显示）
        parent: 父窗口
        on_selected: 选择完成回调函数，接收选中的用户数据
        on_cancelled: 取消选择回调函数
    """
    from qfluentwidgets import Flyout, FlyoutAnimationType
    
    def create_flyout():
        flyout = UserSelectorFlyout(candidates, parent)
        
        if on_selected:
            flyout.userSelected.connect(on_selected)
        
        if on_cancelled:
            flyout.userCancelled.connect(on_cancelled)
        
        return flyout
    
    # 显示 Flyout（使用窗口中心位置）
    flyout_view = create_flyout()
    
    # 使用窗口本身作为目标，让 Flyout 居中显示
    # QFluentWidgets 的 Flyout 会自动定位到 target_widget 的下方
    # 要完全居中，我们需要创建一个临时的中心 widget
    from PyQt5.QtWidgets import QWidget
    from PyQt5.QtCore import Qt
    
    # 创建一个临时 widget 在窗口中心
    center_widget = QWidget(parent)
    center_widget.setFixedSize(1, 1)  # 很小的尺寸
    center_widget.setAttribute(Qt.WA_TransparentForMouseEvents)  # 不响应鼠标
    
    # 定位到窗口中心
    parent_rect = parent.geometry()
    center_widget.move(
        parent_rect.center().x() - 1,
        parent_rect.center().y() - 1
    )
    center_widget.show()
    
    # 使用自定义位置显示
    Flyout.make(flyout_view, center_widget, parent, aniType=FlyoutAnimationType.DROP_DOWN)
    
    # 立即隐藏临时 widget（Flyout 已经定位完成）
    center_widget.hide()
