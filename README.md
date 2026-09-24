# 茶叶拼配审评台

审评员对一个拼配批次打香气、滋味、汤色。服务端按 0.3 / 0.5 / 0.2 加权，7 分及以上通过。提交后只替换表格里新的一行，不整页跳转。

这是制茶审评，不是菜谱，也不是饮食记录。

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3192 |
| 应用 | http://localhost:8192 |
| PostgreSQL | localhost:54392 |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| taster | tea123456 | 可审评 |
| observer | look123456 | 只看 |

## 启动

```bash
cd projects/13-tea-blend-cupping
docker compose up --build
```

## 验收

1. taster 登录后看到春茶-A 通过、夏茶-C 不通过。
2. 再提交一组高分，新行出现在表头，页面不整页刷新。
3. observer 登录后没有提交表单。

## 自动化核对

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

对边界七分、明显高于七、明显低于七三组输入，分别核对判定函数、库内结论、总表色块、详情说明四处逐字一致；并核对只读账号无法提交（页面无表单、直接 POST 返回 403 且不落库）。
