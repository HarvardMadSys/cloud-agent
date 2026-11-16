// Models a deployment provides: the read-only list an owner sees in
// Settings → Models (`provided.*`), and the Admin → Settings → Models section
// where an administrator sets them up (`adminModels.*`). Nothing here names a
// provider or a deployment: the connection's own name is the only name shown.
// The owner's list says nothing of where its models come from: `provided.title`
// is its accessible name, and is not drawn.
//
// Amounts are US dollars, written by `@/lib/money` (`{amount}`, `{input}`,
// `{output}`): a price per million tokens, as the provider's listing gives it.

export const providedModelsEn = {
  'provided.title': 'Available models',
  'adminModels.tab': 'Models',
  'adminModels.title': 'Models this deployment provides',
  'adminModels.description':
    'Connections every admitted account can run tasks on with no setup of its own. Add one with a name, a Base URL and an API key: it offers every model the provider lists for that key, so choose the models, and cap the spending, on the key at the provider. The key stays on the control plane and never enters a sandbox. Accounts with no history start on the oldest enabled connection’s first model.',
  'adminModels.featureOff':
    'Model connections are switched off on this deployment. Nothing here is offered to anyone, and a connection cannot be added, verified or enabled until they are switched on.',
  'adminModels.noRelayHosts':
    'No server can run provided models yet. A turn on one waits until a server that reaches the model relay is enrolled.',
  'adminModels.add': 'Add a provided connection',
  'adminModels.empty': 'No provided connections yet.',
  'adminModels.search.label': 'Provided models',
  'adminModels.search.description':
    'Connections this deployment offers to every account, with the models their keys list.',
  'adminModels.error.nameTaken': 'Another provided connection already has this name.',
  'adminModels.error.modelTaken':
    'This key lists {model}, which {name} already provides. A model can be provided by only one connection: limit one of the keys at the provider so they do not overlap.',
  'adminModels.error.notFound':
    'This connection no longer exists. Reload the page to see the current list.',
  'adminModels.error.featureOff':
    'Model connections are switched off on this deployment. Switch them on before adding, verifying or enabling a provided connection.',
  'adminModels.error.generic': 'The request failed. Try again.',
  'adminModels.invalid.name': 'Enter a name of up to 64 characters.',
  'adminModels.form.addTitle': 'New provided connection',
  'adminModels.form.editTitle': 'Edit provided connection',
  'adminModels.form.name': 'Name',
  'adminModels.form.nameHint': 'Shown to every account in the model picker.',
  'adminModels.form.keyHint':
    'Write-only: it is encrypted, never shown again and never sent to a sandbox. Use a key dedicated to this deployment: the connection offers every model the provider lists for it, and nothing here limits what accounts spend, so set the models and a budget on the key at the provider.',
  'adminModels.form.verifying':
    'Reading the provider’s models for this key and verifying each one… this can take up to four minutes. Keep this page open.',
  'adminModels.form.createNote':
    'Saving reads the models the provider lists for this key and verifies each with a few small requests the provider may bill. A model no agent can use is kept and offered to nobody. The connection is saved disabled: enable it from its card.',
  'adminModels.form.verifyNote':
    'A new key or Base URL reads the provider’s models again and verifies each with the provider before anything is saved, with a few small requests it may bill.',
  'adminModels.accounts.account': 'Account',
  'adminModels.accounts.model': 'Model',
  'adminModels.usage.title': 'Usage',
  'adminModels.usage.description':
    'What each account used per UTC day and model, as the provider’s answers reported it. Cost is the tokens each answer said it used, at the prices the provider listed for the model at the time: an estimate, not the provider’s bill, and nothing is limited by it. Input tokens include the cached ones.',
  'adminModels.usage.showSection': 'Show usage by day',
  'adminModels.usage.hide': 'Hide usage',
  'adminModels.usage.from': 'From (UTC day)',
  'adminModels.usage.to': 'To (UTC day)',
  'adminModels.usage.show': 'Show usage',
  'adminModels.usage.loading': 'Loading…',
  'adminModels.usage.invalid': 'Choose a first day that is not after the last day.',
  'adminModels.usage.empty': 'No usage was recorded in these days.',
  'adminModels.usage.day': 'Day (UTC)',
  'adminModels.usage.requests': 'Requests',
  'adminModels.usage.inputTokens': 'Input tokens',
  'adminModels.usage.cachedTokens': 'Cached tokens',
  'adminModels.usage.outputTokens': 'Output tokens',
  'adminModels.usage.cost': 'Cost',
  'adminModels.usage.estimated': '{count} estimated',
  'adminModels.usage.cacheWritten': '{count} written to cache',
  'adminModels.usage.estimatedNote':
    'Estimated: the provider’s answer named no usage, or was cut short before it could, so the request was recorded as an estimate of its input and of the output it had produced.',
  'adminModels.usage.truncated':
    'More rows matched than are shown. Choose a shorter range to see the rest.',
  'adminModels.card.enableFeatureOff':
    'It cannot be enabled while model connections are switched off on this deployment.',
  'adminModels.card.keyRefused':
    'The provider has refused the stored API key since {since}. Requests on this connection fail until a working key is saved.',
  'adminModels.card.confirmDelete':
    'Delete {name}? Agent processes running on it stop for every account, and chats that used it will need another model. This does not revoke the key at the provider.',
  'adminModels.card.confirmDisable':
    'Disable {name}? Agent processes running on it stop for every account, and no turn can start on it until it is enabled again.',
  'adminModels.error.listingFailed':
    'The provider’s model list could not be read with this key. Check the Base URL and the key: the provider must list its models at the Base URL’s /models.',
  'adminModels.error.listingEmpty': 'The provider lists no model for this key.',
  'adminModels.error.listingTooLong':
    'The provider lists more models for this key than one connection can offer. Limit the key at the provider to the models this deployment should provide.',
  'adminModels.card.modelsFromKey':
    'The models the provider lists for this key. Change them on the key at the provider, then read them again here.',
  'adminModels.card.refresh': 'Read models again',
  'adminModels.card.refreshing': 'Reading the provider’s models and verifying new ones…',
  'adminModels.card.priceInOut': 'Per million tokens: input {input}, output {output}',
  'adminModels.card.priceCacheRead': 'cached input {amount}',
  'adminModels.card.priceCacheWrite': 'cache writes {amount}',
  'adminModels.card.noListedPrice': 'No listed price',
  'adminModels.card.unpricedNote':
    'The provider lists no price for {models}: their use is recorded in tokens, with no cost.',
  'adminModels.error.noneUsable':
    'Not saved: no model the provider lists for this key could be verified for any agent.',
  'adminModels.form.notVerified': 'No agent could use this model.',
  'adminModels.card.notUsable': 'No agent can use this model, so it is offered to nobody.',
} as const;

export const providedModelsZhCN: Record<keyof typeof providedModelsEn, string> = {
  'provided.title': '可用模型',
  'adminModels.tab': '模型',
  'adminModels.title': '本部署提供的模型',
  'adminModels.description':
    '所有已准入的账号无需任何设置，就能用这些连接运行任务。添加连接只需填写名称、Base URL 和 API key：服务商为这个 key 列出的模型都会提供出去，所以要提供哪些模型、花费上限是多少，都请在服务商那边对这个 key 设置。key 只留在控制面，不会进入沙箱。没有使用记录的账号，默认使用最早启用的那个连接的第一个模型。',
  'adminModels.featureOff':
    '此部署已关闭模型连接功能。这里的内容不会提供给任何人；重新开启之前，无法添加、验证或启用连接。',
  'adminModels.noRelayHosts':
    '目前没有服务器可以运行本部署提供的模型。在有能连到模型中继的服务器加入之前，使用这些模型的对话轮次会一直等待。',
  'adminModels.add': '添加提供的连接',
  'adminModels.empty': '还没有提供的连接。',
  'adminModels.search.label': '提供的模型',
  'adminModels.search.description': '本部署向所有账号提供的连接，以及它们的 key 列出的模型。',
  'adminModels.error.nameTaken': '已有同名的提供的连接。',
  'adminModels.error.modelTaken':
    '这把 key 列出了 {model}，而「{name}」已经提供了这个模型。一个模型只能由一个连接提供，请在服务商那边限定其中一把 key 的模型，让两边不重叠。',
  'adminModels.error.notFound': '此连接已不存在。请重新加载页面查看当前列表。',
  'adminModels.error.featureOff':
    '此部署已关闭模型连接功能。请先开启，再添加、验证或启用提供的连接。',
  'adminModels.error.generic': '请求失败，请重试。',
  'adminModels.invalid.name': '请输入名称，最多 64 个字符。',
  'adminModels.form.addTitle': '新建提供的连接',
  'adminModels.form.editTitle': '编辑提供的连接',
  'adminModels.form.name': '名称',
  'adminModels.form.nameHint': '所有账号都会在模型选择器里看到这个名称。',
  'adminModels.form.keyHint':
    '只写：加密保存，之后不再显示，也不会发到沙箱。请使用专供此部署的 key：服务商为它列出的模型都会提供出去，这里也不限制账号的花费，所以请在服务商那边给这个 key 设好可用模型和预算。',
  'adminModels.form.verifying':
    '正在读取服务商为这个 key 列出的模型，并逐个验证……最长可能需要四分钟，请不要关闭此页面。',
  'adminModels.form.createNote':
    '保存时会读取服务商为这个 key 列出的模型，并为每个模型发几个小请求进行验证，服务商可能对此计费。没有任何 Agent 能用的模型会保留下来，但不提供给任何人。连接保存后处于停用状态，请在它的卡片上启用。',
  'adminModels.form.verifyNote':
    '更换 key 或 Base URL 时，会重新读取服务商的模型，并逐个向服务商验证后才保存，服务商可能对这几个小请求计费。',
  'adminModels.accounts.account': '账号',
  'adminModels.accounts.model': '模型',
  'adminModels.usage.title': '用量',
  'adminModels.usage.description':
    '每个账号在每个 UTC 日、每个模型上的用量，数据来自服务商的应答。费用是每次应答报告的 token 数，按服务商当时给这个模型标的价格算出来的估计值，不是服务商的账单，也不用来限制使用。输入 token 包含缓存的部分。',
  'adminModels.usage.showSection': '按天显示用量',
  'adminModels.usage.hide': '收起用量',
  'adminModels.usage.from': '起始日（UTC）',
  'adminModels.usage.to': '结束日（UTC）',
  'adminModels.usage.show': '显示用量',
  'adminModels.usage.loading': '加载中…',
  'adminModels.usage.invalid': '起始日不能晚于结束日。',
  'adminModels.usage.empty': '这些天没有用量记录。',
  'adminModels.usage.day': '日期（UTC）',
  'adminModels.usage.requests': '请求次数',
  'adminModels.usage.inputTokens': '输入 token',
  'adminModels.usage.cachedTokens': '缓存 token',
  'adminModels.usage.outputTokens': '输出 token',
  'adminModels.usage.cost': '费用',
  'adminModels.usage.estimated': '其中 {count} 次按估算',
  'adminModels.usage.cacheWritten': '写入缓存 {count}',
  'adminModels.usage.estimatedNote':
    '按估算：服务商的应答没有给出用量，或在给出之前就中断了，所以这次请求按估算的输入和已经生成的输出记录。',
  'adminModels.usage.truncated': '匹配的行数超过了显示的数量。请缩短日期范围查看其余部分。',
  'adminModels.card.enableFeatureOff': '此部署已关闭模型连接功能，暂时无法启用。',
  'adminModels.card.keyRefused':
    '服务商自 {since} 起拒绝了已保存的 API key。在保存可用的 key 之前，此连接上的请求都会失败。',
  'adminModels.card.confirmDelete':
    '删除 {name}？所有账号在它上面运行的 Agent 进程都会停止，使用它的会话需要选择其他模型。此操作不会在服务商处撤销密钥。',
  'adminModels.card.confirmDisable':
    '停用 {name}？所有账号在它上面运行的 Agent 进程都会停止，重新启用之前无法在它上面开始新的对话轮次。',
  'adminModels.error.listingFailed':
    '用这个 key 读不到服务商的模型列表。请检查 Base URL 和 key：服务商需要在 Base URL 下的 /models 列出模型。',
  'adminModels.error.listingEmpty': '服务商没有为这个 key 列出任何模型。',
  'adminModels.error.listingTooLong':
    '服务商为这个 key 列出的模型超过了一个连接能提供的数量。请在服务商那边把这个 key 限制为本部署要提供的模型。',
  'adminModels.card.modelsFromKey':
    '这些是服务商为这个 key 列出的模型。要增减模型，请在服务商那边修改 key，再回到这里重新读取。',
  'adminModels.card.refresh': '重新读取模型',
  'adminModels.card.refreshing': '正在读取服务商的模型，并验证新增的模型……',
  'adminModels.card.priceInOut': '每百万 token：输入 {input}，输出 {output}',
  'adminModels.card.priceCacheRead': '缓存读取 {amount}',
  'adminModels.card.priceCacheWrite': '缓存写入 {amount}',
  'adminModels.card.noListedPrice': '服务商未标价',
  'adminModels.card.unpricedNote':
    '服务商没有给 {models} 标价：这些模型的用量只记 token 数，不计费用。',
  'adminModels.error.noneUsable':
    '未保存：服务商为这个 key 列出的模型，没有一个能通过任何 Agent 的验证。',
  'adminModels.form.notVerified': '没有 Agent 能使用此模型。',
  'adminModels.card.notUsable': '没有 Agent 能使用此模型，所以不会提供给任何人。',
};
