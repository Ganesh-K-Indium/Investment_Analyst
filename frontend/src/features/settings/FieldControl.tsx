import React from 'react';
import type { FieldSpec } from '../../services/settingsApi';
import { ModelPicker } from './ModelPicker';

interface Props {
    spec: FieldSpec;
    value: unknown;
    overridden?: boolean;
    onChange: (value: unknown) => void;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const show = (v: unknown) => (Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : typeof v === 'boolean' ? (v ? 'on' : 'off') : String(v));

/** One setting rendered from its schema entry: type decides the control, bounds and options come from the server. */
export const FieldControl: React.FC<Props> = ({ spec, value, onChange }) => {
    const id = `setting-${spec.key}`;
    let control: React.ReactNode;
    switch (spec.type) {
        case 'bool':
            control = (
                <label className="set-switch">
                    <input id={id} type="checkbox" checked={Boolean(value)} onChange={e => onChange(e.target.checked)} />
                    <span className="set-switch__track" aria-hidden />
                    <span className="set-switch__text">{value ? 'On' : 'Off'}</span>
                </label>
            );
            break;
        case 'int':
        case 'float':
            control = (
                <input
                    id={id}
                    className="set-input set-input--num"
                    type="number"
                    value={value === undefined || value === null ? '' : String(value)}
                    min={spec.min}
                    max={spec.max}
                    step={spec.type === 'float' ? 0.05 : 1}
                    onChange={e => {
                        const n = e.target.value === '' ? NaN : Number(e.target.value);
                        if (!Number.isNaN(n)) onChange(spec.type === 'int' ? Math.round(n) : n);
                    }}
                />
            );
            break;
        case 'model':
            control = <ModelPicker id={id} value={String(value ?? '')} onChange={onChange} restrictTo={spec.options} />;
            break;
        case 'enum':
            control = (
                <select id={id} className="set-input" value={String(value ?? '')} onChange={e => onChange(e.target.value)}>
                    {(spec.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
                </select>
            );
            break;
        case 'text':
            control = <input id={id} className="set-input" type="text" maxLength={200} value={String(value ?? '')} onChange={e => onChange(e.target.value)} />;
            break;
        case 'list': {
            const current = Array.isArray(value) ? (value as string[]) : [];
            const options = spec.options ?? [];
            control = options.length ? (
                <div className="set-chips" role="group" aria-label={spec.label}>
                    {options.map(o => {
                        const on = current.includes(o);
                        return (
                            <button
                                key={o}
                                type="button"
                                className={`set-chip${on ? ' set-chip--on' : ''}`}
                                aria-pressed={on}
                                onClick={() => onChange(on ? current.filter(x => x !== o) : [...current, o])}
                            >
                                {o.replace(/_/g, ' ')}
                            </button>
                        );
                    })}
                </div>
            ) : (
                <input id={id} className="set-input" type="text" placeholder="comma separated" value={current.join(', ')}
                    onChange={e => onChange(e.target.value.split(',').map(s => s.trim()).filter(Boolean))} />
            );
            break;
        }
        default:
            return null;
    }
    return (
        <div className="set-field">
            <div className="set-field__label">
                <label htmlFor={id}>{spec.label}</label>
                {spec.help && <p className="set-field__help">{spec.help}</p>}
                {!same(value, spec.default) && (
                    <p className="set-field__default">
                        Default is {show(spec.default)}.{' '}
                        <button type="button" className="set-link" onClick={() => onChange(spec.default)}>Use default</button>
                    </p>
                )}
            </div>
            <div className="set-field__control">{control}</div>
        </div>
    );
};
