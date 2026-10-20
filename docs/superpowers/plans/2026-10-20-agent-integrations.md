# Integrações de agentes

Este documento registra as decisões da implementação na branch `feat/agent-integrations`. As instruções de uso e extensão do catálogo estão em [agent-integrations.md](../../agent-integrations.md).

## Objetivo

Usar um catálogo comum para instalar a skill e configurar o MCP, com aprovação independente para cada operação. Adicionar um cliente com formato já suportado deve exigir uma entrada no catálogo e testes, sem alterar o fluxo principal.

## Organização

| Módulo | Responsabilidade |
| --- | --- |
| `agents/catalog.py` | Perfis tipados e regras de localização |
| `agents/detection.py` | Evidências de configuração, caminhos existentes e destinos compartilhados |
| `agents/configuration.py` | Preparação das alterações JSON/TOML em memória |
| `agents/writes.py` | Backup, comparação do estado aprovado e gravação MCP |
| `installer.py` | Cópia da extensão e da skill, prévias e perguntas |
| `main.py` | Encaminhamento dos argumentos de instalação |

Codex e Antigravity App/IDE têm configuração MCP automática. Claude Code e Cursor têm instalação de skills e configuração MCP manual. Os caminhos e as fontes oficiais estão na documentação de integrações.

A implementação suporta instalações globais no Windows e Linux, com Python >=3.12. Usa dependências pure Python; tomlkit preserva a estrutura e os comentários do TOML. Não altera as ferramentas SVG nem o protocolo entre o servidor e a extensão.

## Regras de comportamento

- A detecção apenas consulta caminhos conhecidos. A existência de uma pasta indica configuração disponível, sem comprovar a instalação do aplicativo.
- `--agent` seleciona os perfis; `--dry-run` não cria arquivos, pastas ou backups.
- `--yes` aceita instalação e atualização de skills. Criar outra cópia quando já existe uma instalação em um caminho alternativo exige confirmação explícita.
- Skill e MCP têm aprovações separadas. MCP requer confirmação por arquivo, com resposta padrão negativa e terminal interativo.
- Destinos compartilhados recebem uma operação. Instalações em diretórios diferentes são mantidas; não há migração silenciosa.
- Atualizações de skills mantêm arquivos adicionais e preparam uma cópia antes da substituição. Uma falha de substituição restaura a instalação anterior ou informa onde ela foi preservada para recuperação manual.
- MCP gerencia a entrada `tinta`, com o executável uvx resolvido e o argumento `tinta-mcp`. Outros servidores e campos compatíveis são mantidos.
- Configurações equivalentes não são regravadas. Arquivos inválidos, JSONC e conexões incompatíveis exigem edição manual.
- Prévias não exibem credenciais existentes. Backups preservam os bytes e as permissões do arquivo original.
- A gravação compara o arquivo atual com o estado aprovado antes de substituir. Essa checagem não oferece um bloqueio universal entre aplicativos.
- Cada arquivo é tratado separadamente. Não há transação global, restauração automática de todos os clientes ou sincronização contínua.

## Verificação

Os testes usam configurações sintéticas e diretórios temporários. Cobrem detecção, caminhos compartilhados e antigos, aprovações, prévias, preservação de conteúdo, falhas de cópia, recuperação e repetição sem alterações extras.

A validação inclui a suíte Windows, testes das integrações em Linux, Ruff, Pyright, auditoria de dependências, construção de wheel/sdist, verificação de assets e metadados e instalação isolada do wheel. No Windows, um teste específico verifica as ACLs do backup e do arquivo MCP substituído.

A revisão retrospectiva identificou a atualização destrutiva das skills, uma mensagem de recarregamento específica do Antigravity e um atributo sem uso. As correções preservam a arquitetura e acrescentam testes para esses comportamentos.

## Fora deste escopo

Configurações por projeto, agentes em navegador ou ambientes remotos, plugins externos de integração e perfis próprios para Gemini CLI e Antigravity CLI ficam para outra entrega. A preparação de uma versão, os commits e a publicação são etapas separadas desta implementação.
