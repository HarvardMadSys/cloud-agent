// Models a deployment provides: the read-only list an owner sees in
// Settings → Models (`provided.*`), and the Admin → Settings → Models section
// where an administrator sets them up (`adminModels.*`). Nothing here names a
// provider or a deployment: the connection's own name is the only name shown.
// The owner's list says nothing of where its models come from: `provided.title`
// is its accessible name, and is not drawn.
//
// Amounts are US dollars, written by `@/lib/money` (`{amount}`, `{price}`,
// `{max}`): a daily budget, and a price per million tokens.

export const providedModelsEn = {
  'provided.title': 'Available models',
  'adminModels.tab': 'Models',
  'adminModels.title': 'Models this deployment provides',
  'adminModels.description':
    'Connections every admitted account can run tasks on with no setup of its own. The API key stays on the control plane and never enters a sandbox. Each request is charged the tokens it used at its model’s prices, against the daily budget each account has on the connection. Choose that budget and save every model’s prices, then enable the connection.',
  'adminModels.featureOff':
    'Model connections are switched off on this deployment. Nothing here is offered to anyone, and a connection cannot be added, verified or enabled until they are switched on.',
  'adminModels.noRelayHosts':
    'No server can run provided models yet. A turn on one waits until a server that reaches the model relay is enrolled.',
  'adminModels.add': 'Add a provided connection',
  'adminModels.empty': 'No provided connections yet.',
  'adminModels.search.label': 'Provided models',
  'adminModels.search.description':
    'Connections this deployment offers to every account, with a daily budget per account and prices per model.',
  'adminModels.error.nameTaken': 'Another provided connection already has this name.',
  'adminModels.error.notFound':
    'This connection no longer exists. Reload the page to see the current list.',
  'adminModels.error.termsMissing':
    'Not enabled: the connection needs a daily budget per account and prices for every model first.',
  'adminModels.error.modelNotListed':
    'That model is no longer listed on this connection. Reload the page to see the current list.',
  'adminModels.error.accountNotFound': 'No account has that email address or id.',
  'adminModels.error.featureOff':
    'Model connections are switched off on this deployment. Switch them on before adding, verifying or enabling a provided connection.',
  'adminModels.error.generic': 'The request failed. Try again.',
  'adminModels.invalid.name': 'Enter a name of up to 64 characters.',
  'adminModels.invalid.defaultModel': 'Choose one of the listed models, or none.',
  'adminModels.invalid.header':
    'Enter a header name of up to 64 characters, such as X-End-User, without spaces or a colon.',
  'adminModels.invalid.userConcurrency': 'Enter a whole number from 1 to 64.',
  'adminModels.invalid.budget':
    'Enter an amount in US dollars from 0 to {max}, with at most six decimal places.',
  'adminModels.form.addTitle': 'New provided connection',
  'adminModels.form.editTitle': 'Edit provided connection',
  'adminModels.form.name': 'Name',
  'adminModels.form.nameHint': 'Shown to every account in the model picker.',
  'adminModels.form.keyHint':
    'Write-only: it is encrypted, never shown again and never sent to a sandbox. Use a key dedicated to this deployment, with a budget and model list set at the provider.',
  'adminModels.form.defaultModel': 'Default model',
  'adminModels.form.defaultModelHint':
    'The model an account with no history starts on, when it may use it.',
  'adminModels.form.noDefaultModel': 'None',
  'adminModels.form.userConcurrency': 'Requests in flight per account',
  'adminModels.form.userConcurrencyHint':
    'How many requests one account may have running at once, from 1 to {max}. Each relay process counts its own.',
  'adminModels.form.budget': 'Daily budget per account (USD)',
  'adminModels.form.budgetHint':
    'What each account may spend per UTC day on this connection’s models together. 0 offers the connection only to accounts given a budget of their own. The connection cannot be enabled until a budget is chosen.',
  'adminModels.form.header': 'End-user header (optional)',
  'adminModels.form.headerHint':
    'When set, each relayed request carries this header with an opaque tag for the account, so the provider can attribute or limit use per account. The tag is never an email address or an account id.',
  'adminModels.form.isDefault': 'Default for new users',
  'adminModels.form.isDefaultHint':
    'An account with no history starts on this connection. Only one connection can be the default.',
  'adminModels.form.verifying':
    'Verifying every model with the provider… this can take up to four minutes. Keep this page open.',
  'adminModels.form.createNote':
    'Saving verifies every model with a few small requests the provider may bill. The connection is saved disabled: choose its daily budget and save each model’s prices, then enable it.',
  'adminModels.form.verifyNote':
    'A new key, a new Base URL or an added model is verified with the provider before anything is saved, with a few small requests it may bill.',
  'adminModels.form.defaultFailed':
    'The connection was saved, but it could not be made the default for new users: {reason}',
  'adminModels.terms.title': 'Model prices',
  'adminModels.terms.description':
    'Each request is charged the tokens it used at its model’s prices, against the account’s daily budget. Every listed model needs an input and an output price before the connection can be enabled; a model without prices is offered to nobody. A model marked “Only named accounts” is offered only to accounts you offer it to one by one, under Accounts. A cap on requests in flight is counted by each relay process: a deployment that runs several allows that many in each.',
  'adminModels.terms.unit':
    'Prices are in US dollars per million tokens. A cache price left blank charges those tokens at the input price.',
  'adminModels.terms.inputPrice': 'Input price',
  'adminModels.terms.outputPrice': 'Output price',
  'adminModels.terms.cacheReadPrice': 'Cached input price (optional)',
  'adminModels.terms.cacheWritePrice': 'Cache write price (optional)',
  'adminModels.terms.atInputPrice': 'Same as input',
  'adminModels.terms.namedOnly': 'Only named accounts',
  'adminModels.terms.globalConcurrency': 'Requests in flight across accounts (optional)',
  'adminModels.terms.noCap': 'No cap',
  'adminModels.terms.unset': 'No prices yet: offered to nobody.',
  'adminModels.terms.saved': 'Per million tokens: input {input}, output {output}.',
  'adminModels.terms.savedCacheRead': '{line} Cached input {price}.',
  'adminModels.terms.savedCacheWrite': '{line} Cache writes {price}.',
  'adminModels.terms.savedNamedOnly': '{line} Offered only to named accounts.',
  'adminModels.terms.savedCap': '{line} At most {count} in flight across accounts.',
  'adminModels.terms.saveFor': 'Save the prices for {model}',
  'adminModels.terms.invalidPrices':
    'Enter an input price and an output price, each from 0 to {max} with at most six decimal places.',
  'adminModels.terms.invalidCache':
    'A cache price is above 0 and at most {max}, with at most six decimal places. Leave it blank to charge those tokens at the input price.',
  'adminModels.terms.invalidCap':
    'Requests in flight is a whole number from 1 to {concurrency}, or blank for no cap.',
  'adminModels.accounts.title': 'Accounts',
  'adminModels.accounts.description':
    'Accounts with a daily budget of their own, with a model offered or withheld by name, or with use today. Everyone else follows the connection’s budget and each model’s default.',
  'adminModels.accounts.show': 'Show accounts and their budgets',
  'adminModels.accounts.hide': 'Hide accounts',
  'adminModels.accounts.empty':
    'No account has a budget of its own or a model offered or withheld by name, and none has used a model today.',
  'adminModels.accounts.account': 'Account',
  'adminModels.accounts.accountPlaceholder': 'Email address or account id',
  'adminModels.accounts.model': 'Model',
  'adminModels.accounts.ownBudget': 'Own daily budget',
  'adminModels.accounts.spentToday': 'Spent today',
  'adminModels.accounts.requestsToday': 'Requests today',
  'adminModels.accounts.ownAccess': 'Own model access',
  'adminModels.accounts.followsDefault': 'Follows the default',
  'adminModels.accounts.followsModels': 'Follows each model’s default',
  'adminModels.accounts.withheldBudget': '{amount} (connection withheld)',
  'adminModels.accounts.offered': 'Offered',
  'adminModels.accounts.withheld': 'Withheld',
  'adminModels.accounts.budgetTitle': 'Set a daily budget for an account',
  'adminModels.accounts.budget': 'Daily budget (USD)',
  'adminModels.accounts.budgetHint':
    'Replaces the connection’s default for this account. 0 withholds the connection from it.',
  'adminModels.accounts.setBudget': 'Set budget',
  'adminModels.accounts.accessTitle': 'Offer or withhold a model for an account',
  'adminModels.accounts.access': 'Access',
  'adminModels.accounts.offer': 'Offer the model',
  'adminModels.accounts.withhold': 'Withhold the model',
  'adminModels.accounts.accessHint':
    'Replaces the model’s default for this account: open to every account with a budget, or only to named accounts.',
  'adminModels.accounts.setAccess': 'Save access',
  'adminModels.accounts.clear': 'Clear',
  'adminModels.accounts.clearBudgetFor': 'Put {account} back on the default budget',
  'adminModels.accounts.clearAccessFor': 'Put {account} back on the default for {model}',
  'adminModels.accounts.loading': 'Loading accounts…',
  'adminModels.accounts.more': 'Load more accounts',
  'adminModels.usage.title': 'Usage',
  'adminModels.usage.description':
    'What each account used per UTC day and model, as the provider’s answers reported it. Cost is the tokens each answer said it used, at the model’s prices at the time: it is what a daily budget counts, not the provider’s bill. Input tokens include the cached ones.',
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
    'Estimated: the provider’s answer named no usage, or was cut short before it could, so the request was charged an estimate of its input and of the output it had produced.',
  'adminModels.usage.truncated':
    'More rows matched than are shown. Choose a shorter range to see the rest.',
  'adminModels.card.isDefault': 'Default for new users',
  'adminModels.card.defaultModel': 'Default model: {model}',
  'adminModels.card.budget': 'Daily budget per account: {amount}',
  'adminModels.card.budgetNamed':
    'Daily budget per account: {amount} (only accounts given their own)',
  'adminModels.card.budgetUnset': 'No daily budget chosen',
  'adminModels.card.userConcurrency': 'Up to {count} requests in flight per account',
  'adminModels.card.header': 'End-user header: {header}',
  'adminModels.card.enableNeedsBudget':
    'It cannot be enabled until a daily budget per account is chosen. Edit the connection to choose one.',
  'adminModels.card.enableNeedsPrices':
    'It cannot be enabled until every model has prices. Still without prices: {models}.',
  'adminModels.card.enableNeedsBoth':
    'It cannot be enabled until a daily budget per account is chosen and every model has prices. Edit the connection to choose the budget. Still without prices: {models}.',
  'adminModels.card.enableFeatureOff':
    'It cannot be enabled while model connections are switched off on this deployment.',
  'adminModels.card.offersNothing':
    'This connection is enabled but offers nothing to anyone: no daily budget per account has been chosen. Edit the connection to choose one.',
  'adminModels.card.modelsUnoffered':
    'This connection is enabled, but a model without prices is offered to nobody. Still without prices: {models}.',
  'adminModels.card.keyRefused':
    'The provider has refused the stored API key since {since}. Requests on this connection fail until a working key is saved.',
  'adminModels.card.confirmDelete':
    'Delete {name}? Agent processes running on it stop for every account, and chats that used it will need another model. This does not revoke the key at the provider.',
  'adminModels.card.confirmDisable':
    'Disable {name}? Agent processes running on it stop for every account, and no turn can start on it until it is enabled again.',
} as const;

export const providedModelsZhCN: Record<keyof typeof providedModelsEn, string> = {
  'provided.title': '可用模型',
  'adminModels.tab': '模型',
  'adminModels.title': '本部署提供的模型',
  'adminModels.description':
    '每个已准入的账号无需自行设置就能用来运行任务的连接。API key 只留在控制面，不会进入沙箱。每次请求按它用掉的 token 和模型的价格计费，计入每个账号在这个连接上的每日额度。选定每日额度并为每个模型设置价格后，再启用连接。',
  'adminModels.featureOff':
    '此部署已关闭模型连接功能。这里的内容不会提供给任何人；重新开启之前，无法添加、验证或启用连接。',
  'adminModels.noRelayHosts':
    '目前没有服务器可以运行本部署提供的模型。在有能连到模型中继的服务器加入之前，使用这些模型的对话轮次会一直等待。',
  'adminModels.add': '添加提供的连接',
  'adminModels.empty': '还没有提供的连接。',
  'adminModels.search.label': '提供的模型',
  'adminModels.search.description':
    '本部署向所有账号提供的连接，每个账号有每日额度，每个模型有价格。',
  'adminModels.error.nameTaken': '已有同名的提供的连接。',
  'adminModels.error.notFound': '此连接已不存在。请重新加载页面查看当前列表。',
  'adminModels.error.termsMissing': '未启用：要先选定每个账号的每日额度，并为每个模型设置价格。',
  'adminModels.error.modelNotListed': '此连接已不再列出该模型。请重新加载页面查看当前列表。',
  'adminModels.error.accountNotFound': '没有使用该邮箱或 ID 的账号。',
  'adminModels.error.featureOff':
    '此部署已关闭模型连接功能。请先开启，再添加、验证或启用提供的连接。',
  'adminModels.error.generic': '请求失败，请重试。',
  'adminModels.invalid.name': '请输入名称，最多 64 个字符。',
  'adminModels.invalid.defaultModel': '请选择列表中的一个模型，或不选。',
  'adminModels.invalid.header':
    '请输入请求头名称，最多 64 个字符，例如 X-End-User，不含空格和冒号。',
  'adminModels.invalid.userConcurrency': '请输入 1 到 64 之间的整数。',
  'adminModels.invalid.budget': '请输入 0 到 {max} 之间的美元金额，最多六位小数。',
  'adminModels.form.addTitle': '新建提供的连接',
  'adminModels.form.editTitle': '编辑提供的连接',
  'adminModels.form.name': '名称',
  'adminModels.form.nameHint': '所有账号都会在模型选择器里看到这个名称。',
  'adminModels.form.keyHint':
    '只写：加密保存，之后不再显示，也不会发送到沙箱。请使用专供此部署的 key，并在服务商处设置预算和可用模型。',
  'adminModels.form.defaultModel': '默认模型',
  'adminModels.form.defaultModelHint': '没有历史记录的账号在可以使用它时，默认从这个模型开始。',
  'adminModels.form.noDefaultModel': '无',
  'adminModels.form.userConcurrency': '每个账号进行中的请求数',
  'adminModels.form.userConcurrencyHint':
    '一个账号同时可以有多少个请求在进行，1 到 {max}。每个中继进程各自统计。',
  'adminModels.form.budget': '每个账号的每日额度（美元）',
  'adminModels.form.budgetHint':
    '每个账号每个 UTC 日在这个连接的所有模型上一共可以花费的金额。填 0 表示只提供给单独设置了额度的账号。选定额度之前，连接无法启用。',
  'adminModels.form.header': '用户标签请求头（可选）',
  'adminModels.form.headerHint':
    '设置后，每个中继请求都会带上这个请求头，值是代表该账号的不透明标签，服务商可以据此按账号归因或限额。标签不包含邮箱和账号 ID。',
  'adminModels.form.isDefault': '新用户的默认连接',
  'adminModels.form.isDefaultHint':
    '没有历史记录的账号从这个连接开始。整个部署只能有一个默认连接。',
  'adminModels.form.verifying': '正在向服务商验证每个模型……最长可能需要四分钟，请不要关闭此页面。',
  'adminModels.form.createNote':
    '保存时会为每个模型向服务商发送几个小请求进行验证，服务商可能对此计费。连接保存后处于停用状态：选定每日额度并为每个模型设置价格后再启用。',
  'adminModels.form.verifyNote':
    '更换 key、更换 Base URL 或增加模型时，会先向服务商发送几个小请求进行验证，然后才保存，服务商可能对此计费。',
  'adminModels.form.defaultFailed': '连接已保存，但未能设为新用户的默认连接：{reason}',
  'adminModels.terms.title': '模型价格',
  'adminModels.terms.description':
    '每次请求按它用掉的 token 和模型的价格计费，计入账号的每日额度。每个模型都有输入价格和输出价格后，连接才能启用；没有价格的模型不会提供给任何人。标为“仅限指定账号”的模型，只提供给在“账号”里单独允许的账号。进行中请求数的上限由每个中继进程各自统计：部署运行多个中继进程时，每个进程各允许这么多。',
  'adminModels.terms.unit':
    '价格是每百万 token 的美元金额。缓存价格留空时，这部分 token 按输入价格计费。',
  'adminModels.terms.inputPrice': '输入价格',
  'adminModels.terms.outputPrice': '输出价格',
  'adminModels.terms.cacheReadPrice': '缓存读取价格（可选）',
  'adminModels.terms.cacheWritePrice': '缓存写入价格（可选）',
  'adminModels.terms.atInputPrice': '同输入价格',
  'adminModels.terms.namedOnly': '仅限指定账号',
  'adminModels.terms.globalConcurrency': '所有账号进行中的请求数（可选）',
  'adminModels.terms.noCap': '不限',
  'adminModels.terms.unset': '还没有设置价格，不会提供给任何人。',
  'adminModels.terms.saved': '每百万 token 价格：输入 {input}，输出 {output}。',
  'adminModels.terms.savedCacheRead': '{line}缓存读取 {price}。',
  'adminModels.terms.savedCacheWrite': '{line}缓存写入 {price}。',
  'adminModels.terms.savedNamedOnly': '{line}只提供给指定账号。',
  'adminModels.terms.savedCap': '{line}所有账号同时最多 {count} 个请求在进行。',
  'adminModels.terms.saveFor': '保存 {model} 的价格',
  'adminModels.terms.invalidPrices':
    '请填写输入价格和输出价格：各为 0 到 {max} 之间的金额，最多六位小数。',
  'adminModels.terms.invalidCache':
    '缓存价格须大于 0 且不超过 {max}，最多六位小数。留空表示这部分 token 按输入价格计费。',
  'adminModels.terms.invalidCap':
    '进行中的请求数须为 1 到 {concurrency} 之间的整数，留空表示不限。',
  'adminModels.accounts.title': '账号',
  'adminModels.accounts.description':
    '单独设置了每日额度或模型权限的账号，以及今天有用量的账号。其他账号使用连接的默认额度和各模型的默认设置。',
  'adminModels.accounts.show': '显示账号及其额度',
  'adminModels.accounts.hide': '收起账号',
  'adminModels.accounts.empty': '没有账号单独设置额度或模型权限，今天也没有账号使用过模型。',
  'adminModels.accounts.account': '账号',
  'adminModels.accounts.accountPlaceholder': '邮箱或账号 ID',
  'adminModels.accounts.model': '模型',
  'adminModels.accounts.ownBudget': '单独的每日额度',
  'adminModels.accounts.spentToday': '今日已用',
  'adminModels.accounts.requestsToday': '今日请求次数',
  'adminModels.accounts.ownAccess': '单独的模型权限',
  'adminModels.accounts.followsDefault': '使用默认额度',
  'adminModels.accounts.followsModels': '使用各模型的默认设置',
  'adminModels.accounts.withheldBudget': '{amount}（不提供此连接）',
  'adminModels.accounts.offered': '允许',
  'adminModels.accounts.withheld': '不允许',
  'adminModels.accounts.budgetTitle': '为账号单独设置每日额度',
  'adminModels.accounts.budget': '每日额度（美元）',
  'adminModels.accounts.budgetHint':
    '设置后，这个账号不再使用连接的默认额度。填 0 表示不向它提供此连接。',
  'adminModels.accounts.setBudget': '设置额度',
  'adminModels.accounts.accessTitle': '单独允许或不允许账号使用某个模型',
  'adminModels.accounts.access': '模型权限',
  'adminModels.accounts.offer': '允许使用此模型',
  'adminModels.accounts.withhold': '不允许使用此模型',
  'adminModels.accounts.accessHint':
    '设置后，这个账号不再使用模型的默认设置（对所有有额度的账号开放，或仅限指定账号）。',
  'adminModels.accounts.setAccess': '保存权限',
  'adminModels.accounts.clear': '清除',
  'adminModels.accounts.clearBudgetFor': '让 {account} 恢复使用默认额度',
  'adminModels.accounts.clearAccessFor': '让 {account} 在 {model} 上恢复使用默认设置',
  'adminModels.accounts.loading': '正在加载账号…',
  'adminModels.accounts.more': '加载更多账号',
  'adminModels.usage.title': '用量',
  'adminModels.usage.description':
    '每个账号在每个 UTC 日、每个模型上的用量，数据来自服务商的应答。费用是每次应答报告的 token 数按当时的模型价格算出的金额：它用来计入每日额度，不是服务商的账单。输入 token 包含缓存的部分。',
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
    '按估算：服务商的应答没有给出用量，或在给出之前就中断了，这些请求按估算的输入和已经生成的输出计费。',
  'adminModels.usage.truncated': '匹配的行数超过了显示的数量。请缩短日期范围查看其余部分。',
  'adminModels.card.isDefault': '新用户的默认连接',
  'adminModels.card.defaultModel': '默认模型：{model}',
  'adminModels.card.budget': '每个账号的每日额度：{amount}',
  'adminModels.card.budgetNamed': '每个账号的每日额度：{amount}（仅限单独设置了额度的账号）',
  'adminModels.card.budgetUnset': '尚未选定每日额度',
  'adminModels.card.userConcurrency': '每个账号最多 {count} 个请求同时进行',
  'adminModels.card.header': '用户标签请求头：{header}',
  'adminModels.card.enableNeedsBudget': '选定每个账号的每日额度后才能启用。请编辑连接来选定额度。',
  'adminModels.card.enableNeedsPrices':
    '每个模型都设置价格后才能启用。尚未设置价格的模型：{models}。',
  'adminModels.card.enableNeedsBoth':
    '选定每个账号的每日额度，并为每个模型设置价格后才能启用。请编辑连接来选定额度。尚未设置价格的模型：{models}。',
  'adminModels.card.enableFeatureOff': '此部署已关闭模型连接功能，暂时无法启用。',
  'adminModels.card.offersNothing':
    '此连接已启用，但还没有选定每个账号的每日额度，所以不向任何人提供。请编辑连接来选定额度。',
  'adminModels.card.modelsUnoffered':
    '此连接已启用，但没有价格的模型不会提供给任何人。尚未设置价格的模型：{models}。',
  'adminModels.card.keyRefused':
    '服务商自 {since} 起拒绝了已保存的 API key。在保存可用的 key 之前，此连接上的请求都会失败。',
  'adminModels.card.confirmDelete':
    '删除 {name}？所有账号在它上面运行的 Agent 进程都会停止，使用它的会话需要选择其他模型。此操作不会在服务商处撤销密钥。',
  'adminModels.card.confirmDisable':
    '停用 {name}？所有账号在它上面运行的 Agent 进程都会停止，重新启用之前无法在它上面开始新的对话轮次。',
};
