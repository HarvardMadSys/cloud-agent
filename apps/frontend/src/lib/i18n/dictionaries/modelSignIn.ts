// Signing in at the deployment's model provider for a key of the owner's own:
// the button on the Models page and in the composer, and the callback page.

export const modelSignInEn = {
  'modelSignIn.action': 'Create or link a {name} account',
  'modelSignIn.leaving': 'Opening {name}…',
  'modelSignIn.description':
    'Sign up at {name}, or sign in to the account you have there, and approve a key for this account. It comes back as a connection of your own, already verified. {name} manages its billing and usage limits.',
  'modelSignIn.noticeDescription':
    'Create a {name} account, or link the one you have, to get a key of your own in one step. You can also add the Base URL, API key and model ID from another provider.',
  'modelSignIn.manual': 'Add a connection by hand',
  'modelSignIn.startFailed': 'Could not start signing in. Try again.',
  'modelSignIn.finishing': 'Saving your new model connection…',
  'modelSignIn.failedTitle': 'Could not connect your model provider',
  'modelSignIn.missingCode':
    'The provider did not return a code. Nothing was saved; start again from the Models page.',
  'modelSignIn.missingState':
    'The provider did not return the sign-in state. Nothing was saved; start again from the Models page.',
  'modelSignIn.denied': 'No key was approved at the provider, so nothing was saved.',
  'modelSignIn.finishFailed':
    'The connection could not be saved. Start again from the Models page.',
  'modelSignIn.back': 'Back to Models',
  'modelSignIn.savedTitle': '{name} is connected',
  'modelSignIn.skipped':
    'Left off: {models}. Each is already on another of your connections, or no agent can use it with this key.',
  'modelSignIn.continue': 'Continue',
} as const;

export const modelSignInZhCN: Record<keyof typeof modelSignInEn, string> = {
  'modelSignIn.action': '创建或关联 {name} 账号',
  'modelSignIn.leaving': '正在打开 {name}…',
  'modelSignIn.description':
    '在 {name} 注册，或登录你在那里已有的账号，并为此账号批准一个 key。回来后它会成为你自己的连接，并且已经验证。费用和使用额度由 {name} 管理。',
  'modelSignIn.noticeDescription':
    '创建 {name} 账号或关联已有账号，一步获得你自己的 key。也可以填写其他服务商的 Base URL、API key 和模型 ID。',
  'modelSignIn.manual': '手动添加连接',
  'modelSignIn.startFailed': '无法开始登录，请重试。',
  'modelSignIn.finishing': '正在保存新的模型连接…',
  'modelSignIn.failedTitle': '无法连接模型服务商',
  'modelSignIn.missingCode': '服务商没有返回授权码，未保存任何内容。请回到模型页面重新开始。',
  'modelSignIn.missingState': '服务商没有返回登录状态，未保存任何内容。请回到模型页面重新开始。',
  'modelSignIn.denied': '服务商那边没有批准 key，未保存任何内容。',
  'modelSignIn.finishFailed': '无法保存连接。请回到模型页面重新开始。',
  'modelSignIn.back': '返回模型',
  'modelSignIn.savedTitle': '已连接 {name}',
  'modelSignIn.skipped':
    '未添加：{models}。它们已在你的其他连接中，或没有 Agent 能用此 key 使用它们。',
  'modelSignIn.continue': '继续',
};
