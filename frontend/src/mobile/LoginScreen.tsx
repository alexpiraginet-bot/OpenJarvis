/** Entrada do cliente — login ou primeiro acesso. */

import { Fingerprint, ShieldCheck, Waves } from 'lucide-react';
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
        <span>SECURE PERSONAL SYSTEM</span>
        <span className="oj-login-system-id">JL / 05</span>
      </div>

      <div className="oj-login-reactor" aria-hidden="true">
        <span className="oj-login-orbit oj-login-orbit--outer" />
        <span className="oj-login-orbit oj-login-orbit--middle" />
        <span className="oj-login-orbit oj-login-orbit--inner" />
        <span className="oj-login-sweep" />
        <img src="/aether-neural-core.png" alt="" />
        <span className="oj-login-core-pulse" />
        <span className="oj-login-coordinate oj-login-coordinate--left">
          IDENTITY<br />ENCRYPTED
        </span>
        <span className="oj-login-coordinate oj-login-coordinate--right">
          CORE LINK<br />STANDBY
        </span>
      </div>

      <section className="oj-login-console">
        <div className="oj-login-console-head">
          <div>
            <div className="oj-login-code">JARVIS LIFE / IDENTITY GATE</div>
            <h1 className="oj-login-title">
              {mode === 'login' ? 'Acesso ao centro de comando' : 'Ativar seu sistema pessoal'}
            </h1>
          </div>
          <Fingerprint size={28} aria-hidden="true" />
        </div>
        <p className="oj-login-sub">
          {mode === 'login'
            ? 'Identifique-se para sincronizar o núcleo, seus especialistas e toda a sua vida.'
            : 'Finanças, treino, rotina, família e trabalho conectados a um único cérebro por voz.'}
        </p>

        <div className="oj-login-capabilities" aria-label="Capacidades do sistema">
          <span><Waves size={12} /> voz neural</span>
          <span>5 especialistas IA</span>
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
          {busy ? 'Sincronizando…' : mode === 'login' ? 'Entrar no sistema' : 'Ativar Jarvis Life'}
        </Button>

        <button
          type="button"
          className="oj-switch"
          onClick={() => {
            setMode(mode === 'login' ? 'register' : 'login');
            setError('');
          }}
        >
          {mode === 'login' ? 'Criar uma identidade' : 'Já tenho identidade'}
        </button>
      </section>
    </div>
  );
}
