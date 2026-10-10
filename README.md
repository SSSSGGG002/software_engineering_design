# 软件工程课程设计：Wylie College 课程注册系统

五人协作学习参考实现。技术栈：Python / Flask / SQLite / 服务端模板。

## 快速导航

- [安装与运行](course-registration/README.md)
- [阅读与核验指南](docs/00-阅读与核验指南.md)
- [开发计划](docs/01-开发计划.md)
- [需求分析](docs/02-需求分析.md)
- [概要与详细设计](docs/03-概要与详细设计.md)
- [测试计划](docs/04-测试计划.md) · [测试报告](docs/05-测试报告.md)
- [五人分工咬合与接口契约](docs/07-五人分工咬合与接口契约.md)
- [答辩指南与演示脚本](docs/08-答辩指南与演示脚本.md)
- [个人与全组总结待填模板](docs/09-工作总结待填模板.md)
- [需求追踪矩阵](docs/11-需求追踪矩阵.md)
- [文档汇编 PDF](docs/项目文档汇编.pdf)
- [测试与运行证据](docs/evidence/)

## 启动

```bash
cd course-registration
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m flask --app crs init-demo
python run.py
```

访问 http://127.0.0.1:5000 。注册员 admin/admin123；学生 s1/123456；教授 p1/123456。全部为本地演示账号。

```bash
python -m pytest -q --cov=crs --cov-report=term-missing
```

已记录的本地测试：51 项通过，语句覆盖率 88.81%。目录与计费采用独立 SQLite 模拟；2000/500 用户容量、真实外部系统及 Windows 实机未验证。

## 团队衔接

A：架构、身份、目录与集成；B：人员与账号生命周期；C：学生注册；D：授课和成绩；E：注册关闭与计费。

交接链：人员建档 → 身份登录 → 授课认领 → 学生注册 → 花名册 → 关闭补位与计费 → 成绩 → 下一学期先修校验。每个交接有接口契约和共同测试。

## 使用声明

成员A–E是建议角色，工时与20%权重是计划值；实际贡献、姓名、学号与签名须团队据实填写。

首次是否严格4+2、教学结束时间、计费时点等差异已在文档中明确登记，需教师确认。

原始课程附件未纳入公开发布仓库。单页封面适配副本供本地填写，公开发布材料不包含该封面；本地完整交付包另行提供。

ClawsGO Science Agent
