import asyncio
from copilot import CopilotClient, PermissionHandler, define_tool
from pydantic import BaseModel, Field
from typing import Any


class MultiplyParams(BaseModel):
    a: int = Field(description="The first integer to multiply")
    b: int = Field(description="The second integer to multiply")


@define_tool(description="Multiply two integers and return the product")
def multiply_numbers(params: MultiplyParams) -> str:
    product = params.a * params.b
    print(f"[tool] multiply_numbers called with a={params.a}, b={params.b}")
    return str(product)

async def main():
    # Create and start client
    client = CopilotClient()
    await client.start()

    try:
        # create_session parameters, grouped as: Required / Optional / Default Behavior.
        # create_session 参数按以下分组展示：必填 / 可选 / 默认行为。
        session_config: dict[str, Any] = {
            # Required / 必填
            "on_permission_request": PermissionHandler.approve_all,  # Required by this SDK. Handles approval requests for tools/files/commands. / 当前 SDK 强制要求提供，用于处理工具、文件、命令等权限审批。

            # Optional / 可选
            "tools": [multiply_numbers],  # Custom tools exposed to the model for this session. / 暴露给当前会话模型使用的自定义工具。

            # Default Behavior Shown Explicitly / 显式展示默认行为
            "session_id": None,  # No custom session ID; the SDK generates one automatically. / 不自定义会话 ID，由 SDK 自动生成。
            "client_name": None,  # No extra client name override in request metadata. / 不额外覆盖请求中的客户端名称。
            "model": None,  # Use the CLI/server default model. / 使用 CLI 或服务端默认模型。
            "reasoning_effort": None,  # Do not override reasoning level. / 不显式覆盖推理强度。
            "system_message": None,  # Keep the built-in system prompt behavior. / 保持内置 system prompt 行为。
            "available_tools": None,  # Do not apply an extra allowlist. / 不额外设置工具白名单。
            "excluded_tools": None,  # Do not apply an extra denylist. / 不额外设置工具黑名单。
            "on_user_input_request": None,  # Do not handle ask_user-style requests. / 不处理 ask_user 这类用户输入请求。
            "hooks": {},  # Register no session lifecycle hooks. / 不注册会话生命周期 hooks。
            "working_directory": None,  # Use the session/client default working directory. / 使用会话或客户端默认工作目录。
            "provider": None,  # Use the default Copilot provider instead of BYOK. / 不覆盖 provider，继续使用默认 Copilot provider。
            "streaming": False,  # Do not request incremental delta events. / 不开启增量流式事件输出。
            "mcp_servers": {},  # Add no extra MCP servers. / 不额外添加 MCP 服务器。
            "custom_agents": [],  # Add no custom agents. / 不额外添加自定义 agent。
            "config_dir": None,  # Do not override the default config/state directory. / 不覆盖默认配置与状态目录。
            "skill_directories": [],  # Load no extra skill directories. / 不额外加载技能目录。
            "disabled_skills": [],  # Disable no skills. / 不禁用任何技能。
            "infinite_sessions": {
                "enabled": True,  # Default behavior: keep infinite sessions enabled. / 默认保持 infinite sessions 启用。
                "background_compaction_threshold": 0.80,  # Default background compaction threshold. / 默认后台压缩阈值为 0.80。
                "buffer_exhaustion_threshold": 0.95,  # Default blocking compaction threshold. / 默认阻塞式压缩阈值为 0.95。
            },
        }

        # Create a session
        async with await client.create_session(session_config) as session:
            # send_and_wait parameters, grouped as: Required / Optional / Default Behavior.
            # send_and_wait 参数按以下分组展示：必填 / 可选 / 默认行为。
            message_options: dict[str, Any] = {
                # Required / 必填
                "prompt": "Call the multiply_numbers tool to calculate 6 * 7, 用古诗的形式回答结果.",  # Required message text sent to the session. / 发送到当前会话的必填消息文本。

                # Default Behavior Shown Explicitly / 显式展示默认行为
                "attachments": None,  # Send no file, directory, or selection attachments. / 不附加文件、目录或代码选区。
                "mode": None,  # Use the SDK default send mode instead of forcing enqueue/immediate. / 不强制指定 enqueue 或 immediate，沿用 SDK 默认发送模式。
            }

            # Optional / 可选
            send_timeout = 60.0  # Explicitly use the SDK default timeout in seconds. / 显式写出 SDK 默认超时时间 60 秒。

            response = await session.send_and_wait(
                message_options,
                timeout=send_timeout,
            )
            print(response.data.content)
    finally:
        await client.stop()

asyncio.run(main())