import { redirect } from "next/navigation";

interface AgentRootPageProps {
  params: Promise<{
    agent_name: string;
  }>;
}

/**
 * 智能体根路径跳转页。
 *
 * 描述作用:
 *   将 `/workspace/agents/{agent_name}` 规范化到该智能体的新会话入口，避免用户直接访问智能体根路径时看到 404。
 *
 * Args参数说明:
 *   props: Next.js 页面属性，包含动态路由参数 `agent_name`。
 *
 * Return返回值:
 *   Promise<never>: 调用 Next.js `redirect` 后终止当前页面渲染。
 */
export default async function AgentRootPage({
  params,
}: AgentRootPageProps): Promise<never> {
  const { agent_name } = await params;

  // 保留智能体名称语义，同时对路径片段做编码，防止特殊字符破坏跳转 URL。
  return redirect(
    `/workspace/agents/${encodeURIComponent(agent_name)}/chats/new`,
  );
}
