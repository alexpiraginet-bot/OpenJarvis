/** Entrada do cliente — login ou primeiro acesso. */

import { BrainCircuit, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { login, register } from './api';
import type { LifeUser } from './types';
import { Button, Field } from './ui';

export function LoginScreen({ onAuth }: { onAuth: (user: LifeUser) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!email.trim() || !password) {
      setError('Preencha e-mail e senha.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result =
        mode === 'login'
          ? await login(email.trim(), password)
          : await register({ email: email.trim(), password, name: name.trim() });
      onAuth(result.user);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Não consegui entrar.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="oj-login">
      <div className="oj-login-system">
        <span className="oj-system-led" />
        SECURE PERSONAL SYSTEM
      </div>
      <div className="oj-login-mark">
        <BrainCircuit size={34} />
      </div>
      <div className="oj-login-code">JARVIS LIFE / IDENTITY GATE</div>
      <h1 className="oj-login-title">
        {mode === 'login' ? 'Bem-vindo de volta' : 'Sua vida, na palma da mão'}
      </h1>
      <p className="oj-login-sub">
        {mode === 'login'
          ? 'Entre para falar com o seu Jarvis.'
          : 'Finanças, treino, rotina, família e trabalho — em um só lugar, com um assistente que conhece tudo isso.'}
      </p>

      <div className="oj-login-capabilities" aria-label="Capacidades do sistema">
        <span>5 especialistas IA</span>
        <span>voz nativa</span>
        <span><ShieldCheck size={12} /> dados protegidos</span>
      </div>

      {error && <div className="oj-error">{error}</div>}

      {mode === 'register' && (
        <Field label="Nome" value={name} onChange={setName} placeholder="Alex" />
      )}
      <Field
        label="E-mail"
        value={email}
        onChange={setEmail}
        type="email"
        placeholder="voce@exemplo.com"
      />
      <Field
        label="Senha"
        value={password}
        onChange={setPassword}
        type="password"
        placeholder="mínimo 8 caracteres"
      />

      <Button onClick={submit} disabled={busy}>
        {busy ? 'Entrando…' : mode === 'login' ? 'Entrar' : 'Criar conta'}
      </Button>

      <button
        type="button"
        className="oj-switch"
        onClick={() => {
          setMode(mode === 'login' ? 'register' : 'login');
          setError('');
        }}
      >
        {mode === 'login' ? 'Criar uma conta' : 'Já tenho conta'}
      </button>
    </div>
  );
}
