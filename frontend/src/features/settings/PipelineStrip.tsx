import React from 'react';
import type { Settings } from '../../services/settingsApi';
import { PRESETS, matchingPreset, type Defaults, type Preset } from './presets';
import { pipelineSteps } from './summary';

interface Props {
    draft: Settings;
    dirtySections: Set<string>;
    activeSection: string;
    onOpen: (section: string) => void;
    onPreset: (p: Preset) => void;
    defaults: Defaults;
}

/** The path every question takes, with what you have chosen at each stage. Click a stage to change it; pick a preset to fill the form. */
export const PipelineStrip: React.FC<Props> = ({ draft, dirtySections, activeSection, onOpen, onPreset, defaults }) => {
    const steps = pipelineSteps(draft);
    const current = matchingPreset(draft, defaults);
    return (
        <section className="pipe" aria-label="How a question is answered">
            <div className="pipe__head">
                <div>
                    <h2 className="pipe__title">How a question is answered</h2>
                    <p className="pipe__sub">Each stage is yours to tune. Start from a preset, then adjust.</p>
                </div>
                <div className="pipe__presets" role="group" aria-label="Presets">
                    {PRESETS.map(p => (
                        <button key={p.id} type="button" className={`pipe__preset${current?.id === p.id ? ' pipe__preset--on' : ''}`} aria-pressed={current?.id === p.id} onClick={() => onPreset(p)} title={p.detail}>
                            <span className="pipe__preset-name">{p.name}</span>
                            <span className="pipe__preset-tag">{p.tagline}</span>
                        </button>
                    ))}
                    <span className={`pipe__preset pipe__preset--static${current ? '' : ' pipe__preset--on'}`} aria-current={current ? undefined : 'true'}
                        title="Shown when your settings differ from every preset. Pick a preset to start over.">
                        <span className="pipe__preset-name">Custom</span>
                        <span className="pipe__preset-tag">{current ? 'Your own mix' : 'Your changes'}</span>
                    </span>
                </div>
            </div>
            <ol className="pipe__steps">
                {steps.map((s, i) => (
                    <li key={s.id} className="pipe__item">
                        <button type="button" className={`pipe__step${activeSection === s.section ? ' pipe__step--on' : ''}`} onClick={() => onOpen(s.section)} aria-label={`${s.title}: ${s.line}. Open ${s.section} settings`}>
                            <span className="pipe__step-title">{s.title}{dirtySections.has(s.section) && <span className="pipe__dirty" title="Unsaved changes" />}</span>
                            <span className="pipe__step-line">{s.line}</span>
                        </button>
                        {i < steps.length - 1 && <span className="pipe__link" aria-hidden />}
                    </li>
                ))}
            </ol>
        </section>
    );
};
