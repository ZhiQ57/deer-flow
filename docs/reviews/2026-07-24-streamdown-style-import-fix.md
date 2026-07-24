# Streamdown 样式导入构建失败修复 Review

审查日期：2026-07-24

## 1、结论

页面报错由前端依赖目录未随 Git 合并同步引起，源码和锁文件本身匹配，无需删除 `streamdown/styles.css` 导入或修改依赖版本。

合并提交 `bb008812` 将 `streamdown` 从 `1.4.0` 升级到 `2.5.0`，并同时加入该样式导入。`streamdown@2.5.0` 导出 `./styles.css`，而本机旧的 `1.4.0` 只导出包根路径，因此 Next.js 无法解析该模块。

## 2、修复内容

- 在 `frontend/` 使用 `pnpm install --frozen-lockfile` 重新生成本地 `node_modules`，实际安装版本已为 `streamdown@2.5.0`，且 `styles.css` 可解析。
- 将 `message-list.tsx` 的相对导入恢复为 ESLint 要求的顺序，消除合并后暴露的静态检查错误。
- 未修改 `frontend/package.json`、`frontend/pnpm-lock.yaml` 或 Streamdown 的样式导入。

## 3、验证

- `pnpm list streamdown --depth 0`：显示 `streamdown 2.5.0`。
- `node --input-type=module --eval "import.meta.resolve('streamdown/styles.css')"`：成功解析到安装包样式文件。
- `pnpm check`：通过。
- `pnpm build`：通过，81 个静态页面生成完成。
- 重启 `pnpm dev` 后请求 `http://localhost:3000/`：HTTP 200。

`pnpm build` 仍输出 `next.config.js` 的 Turbopack NFT trace 警告；该警告不影响本次编译或页面访问，且与本次依赖同步无关。
