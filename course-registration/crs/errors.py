"""统一的业务异常体系。

服务层只抛出 DomainError 子类，Web 层负责把它们翻译成用户可读的提示，
保证"出错时给出明确信息、系统状态不变"（各用例的后置条件）。

负责人：成员A（架构）
"""


class DomainError(Exception):
    """业务规则异常基类。message 面向最终用户；details 为补充条目列表。"""

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.message = message
        self.details = list(details or [])


class ValidationError(DomainError):
    """输入校验失败。field_errors: {字段名: 错误说明}"""

    def __init__(self, field_errors: dict, message: str = "输入信息有误，请检查后重新提交"):
        super().__init__(message, [f"{v}" for v in field_errors.values()])
        self.field_errors = dict(field_errors)


class AuthenticationError(DomainError):
    pass


class PermissionDeniedError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    """唯一性冲突、并发冲突、被其他人占用等。"""


class RegistrationClosedError(DomainError):
    pass


class RegistrationInProgressError(DomainError):
    pass


class ScheduleRejectedError(DomainError):
    """提交课表被拒（先修课不满足 / 课程已满 / 时间冲突），details 列出每条原因。"""


class ExternalSystemError(DomainError):
    pass


class CatalogUnavailableError(ExternalSystemError):
    def __init__(self, message: str = "课程目录系统暂时无法访问，请稍后再试"):
        super().__init__(message)


class BillingUnavailableError(ExternalSystemError):
    def __init__(self, message: str = "计费系统暂时无法访问"):
        super().__init__(message)
