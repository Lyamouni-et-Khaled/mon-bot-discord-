/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from 'react';
import { createRoot } from 'react-dom/client';

const JsonCard = ({ title, data }) => (
    <div className="card">
        <div className="card-header">
            <h2 className="card-title">{title}</h2>
        </div>
        <pre>{JSON.stringify(data, null, 2)}</pre>
    </div>
);

const getProductPriceDisplay = (p) => {
    const currency = p.currency || 'EUR';
    if (p.price_text) {
        return p.price_text;
    }
    if (p.options && p.options.length > 0) {
        try {
            const prices = p.options.map(opt => opt.price);
            const minPrice = Math.min(...prices);
            return `À partir de ${minPrice.toFixed(2)} ${currency}`;
        } catch (e) {
            return "Prix variable";
        }
    }
    if (p.price != null && p.price >= 0) {
        return `${p.price.toFixed(2)} ${currency}`;
    }
    return 'Prix sur demande';
};

const getCategoryIcon = (category) => {
    const iconStyle = { marginRight: '0.75rem', flexShrink: 0, color: 'var(--primary)', transition: 'color 0.3s ease' };
    switch (category) {
        case 'Services & Avantages Discord':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/><path d="m9.09 9.09.41-3.1L12 7.5l2.5-1.51.41 3.1-2.1 2.1 3.1.41L14.5 12l1.51 2.5-3.1.41-2.1-2.1-.41 3.1L9.5 12l-1.51-2.5z"/></svg>;
        case 'Ebooks & Guides':
        case 'Formations':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>;
        case 'Comptes Premium':
        case 'Services Financiers':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 21v-3.5a2.5 2.5 0 0 1 5 0V21"/><path d="M7 21v-3.5a2.5 2.5 0 0 0-5 0V21"/><rect width="20" height="10" x="2" y="3" rx="2"/><circle cx="8" cy="8" r="1"/><circle cx="16" cy="8" r="1"/><path d="M12 8h.01"/></svg>;
        case 'Gaming - Outils':
        case 'Gaming - Monnaie Virtuelle':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 6.5A2.5 2.5 0 0 1 5 4h14a2 2 0 0 1 2 2v7.5a2.5 2.5 0 0 1-2.5 2.5H5A2.5 2.5 0 0 1 2.5 14Z"/><path d="M6 18h12"/><path d="M10 12h4v-2h-4v2z"/><path d="M10 6.5v-2.5"/><path d="M14 6.5v-2.5"/></svg>;
        case 'Panels':
        case 'Outils & Logiciels':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 14v-2a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v2"/></svg>;
        case 'Services de Création':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 5-3-3-3 3"/><path d="m15 19 3 3 3-3"/><path d="M4 9v6"/><path d="M9 4h6"/><path d="M20 9v6"/><path d="M9 20h6"/></svg>;
        case 'Logs':
             return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>;
        case 'Boost Réseaux Sociaux':
             return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>;
        case 'Fournisseurs & Accès Exclusifs':
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13.4 2H6.6l-2 9h15.2z"/><path d="M22 13v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6h20z"/><path d="M12 17a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/></svg>;
        default:
            return <svg style={iconStyle} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>;
    }
}

const ProductsCard = ({ title, products }) => (
    <div className="card">
        <div className="card-header">
            <h2 className="card-title">{title}</h2>
        </div>
        <div className="grid">
            {products.map(p => (
                <div key={p.id} className="card product-card">
                    <span className="category">{p.category}</span>
                    <div style={{ display: 'flex', alignItems: 'center', margin: '0.5rem 0' }}>
                        {getCategoryIcon(p.category)}
                        <h3 style={{ margin: 0 }}>{p.name}</h3>
                    </div>
                    <p style={{ color: 'var(--text-secondary)', flexGrow: 1, margin: '0.5rem 0' }}>{p.description}</p>
                    <div className="price">{getProductPriceDisplay(p)}</div>
                    <small style={{color: 'var(--text-secondary)'}}>ID: {p.id}</small>
                </div>
            ))}
        </div>
    </div>
);

const TrophyIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--gold)', marginRight: '0.5rem', flexShrink: 0 }}>
        <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/>
        <path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/>
        <path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>
    </svg>
);

const AchievementsCard = ({ title, achievements }) => (
    <div className="card">
        <div className="card-header">
            <h2 className="card-title">{title}</h2>
        </div>
        <div className="grid">
            {achievements.map(a => (
                <div key={a.id} className="card achievement-card">
                     <div style={{ display: 'flex', alignItems: 'center', marginBottom: '0.5rem' }}>
                        <TrophyIcon />
                        <h3 style={{ margin: 0, color: 'var(--gold)' }}>{a.name}</h3>
                     </div>
                     <p style={{ color: 'var(--text-secondary)', margin: '0.5rem 0 1rem 0' }}>{a.description}</p>
                     <p className="reward">Récompense: <strong>{a.reward_xp} XP</strong></p>
                </div>
            ))}
        </div>
    </div>
)

const getPaletteForLevel = (level, profileCardConfig) => {
    if (!profileCardConfig) return null;

    let selectedPalette = profileCardConfig.DEFAULT_PALETTE;
    const sortedPalettes = [...(profileCardConfig.LEVEL_PALETTES || [])].sort((a,b) => b.level - a.level);

    for (const tier of sortedPalettes) {
        if (level >= tier.level) {
            selectedPalette = tier.palette;
            break;
        }
    }
    return selectedPalette;
};


const App = () => {
    const [config, setConfig] = useState(null);
    const [products, setProducts] = useState(null);
    const [achievements, setAchievements] = useState(null);
    const [error, setError] = useState('');
    const [userLevel, setUserLevel] = useState(1);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const [configRes, productsRes, achievementsRes] = await Promise.all([
                    fetch('/config.json'),
                    fetch('/products.json'),
                    fetch('/achievements_config.json')
                ]);

                if (!configRes.ok || !productsRes.ok || !achievementsRes.ok) {
                    throw new Error('Failed to fetch one or more configuration files.');
                }

                setConfig(await configRes.json());
                setProducts(await productsRes.json());
                setAchievements(await achievementsRes.json());
            } catch (err) {
                console.error("Failed to fetch data:", err);
                setError('Could not load bot configuration. Please check the console for more details.');
            }
        };
        fetchData();
    }, []);

    const {
        GAMIFICATION_CONFIG = null,
        MISSION_SYSTEM = null,
        TRANSACTION_LOG_CONFIG = null,
        PROFILE_CARD_CONFIG = null,
        ...restOfConfig
    } = config || {};
    
    // Extract nested systems for individual display
    const AFFILIATE_SYSTEM = GAMIFICATION_CONFIG?.AFFILIATE_SYSTEM || null;
    const GUILD_SYSTEM = GAMIFICATION_CONFIG?.GUILD_SYSTEM || null;

    const activePalette = getPaletteForLevel(userLevel, PROFILE_CARD_CONFIG);

    useEffect(() => {
        if (activePalette) {
            const getCssColor = (colorValue) => Array.isArray(colorValue) ? colorValue[0] : colorValue;
            document.documentElement.style.setProperty('--primary', getCssColor(activePalette.accent));
            document.documentElement.style.setProperty('--background', getCssColor(activePalette.background));
            document.documentElement.style.setProperty('--surface', getCssColor(activePalette.surface));
            document.documentElement.style.setProperty('--text-primary', getCssColor(activePalette.text));
        } else if (config) {
            // Reset to default if no palette found or config changes
            document.documentElement.style.setProperty('--primary', '#3b82f6');
            document.documentElement.style.setProperty('--background', '#111827');
            document.documentElement.style.setProperty('--surface', '#1f2937');
            document.documentElement.style.setProperty('--text-primary', '#f9fafb');
        }
    }, [activePalette, config]);

    if (error) {
        return <div className="card" style={{ color: 'var(--danger)'}}>{error}</div>;
    }
    
    if (!config || !products || !achievements) {
        return <div className="card">Loading configuration...</div>;
    }

    return (
        <>
            <h1>ResellBoost Bot Dashboard</h1>

            <div className="card">
                <div className="card-header" style={{paddingBottom: '1.25rem'}}>
                    <h2 className="card-title">Simulateur de Niveau Utilisateur</h2>
                </div>
                <label htmlFor="level-slider" style={{display: 'block', marginBottom: '0.5rem', color: 'var(--text-secondary)'}}>Faites glisser pour voir le thème visuel changer en fonction du niveau.</label>
                <input 
                    id="level-slider"
                    type="range" 
                    min="1" 
                    max="50" 
                    value={userLevel} 
                    onChange={(e) => setUserLevel(Number(e.target.value))}
                    aria-label="Simulate user level"
                />
                <p style={{textAlign: 'center', margin: '0.5rem 0 0 0', fontWeight: 600, fontSize: '1.25rem'}}>
                    Niveau: <span style={{color: 'var(--primary)', transition: 'color 0.3s ease'}}>{userLevel}</span>
                </p>
            </div>

            <ProductsCard title="Catalogue de Produits" products={products} />
            <AchievementsCard title="Succès" achievements={achievements} />
            {GAMIFICATION_CONFIG && <JsonCard title="Système de Gamification (config.json)" data={GAMIFICATION_CONFIG} />}
            {AFFILIATE_SYSTEM && <JsonCard title="Système d'Affiliation (config.json)" data={AFFILIATE_SYSTEM} />}
            {GUILD_SYSTEM && <JsonCard title="Système de Guildes (config.json)" data={GUILD_SYSTEM} />}
            {MISSION_SYSTEM && <JsonCard title="Système de Missions (config.json)" data={MISSION_SYSTEM} />}
            {TRANSACTION_LOG_CONFIG && <JsonCard title="Journal des Transactions (config.json)" data={TRANSACTION_LOG_CONFIG} />}
            {PROFILE_CARD_CONFIG && <JsonCard title="Configuration Cartes de Profil (config.json)" data={PROFILE_CARD_CONFIG} />}
            <JsonCard title="Configuration Générale (config.json)" data={restOfConfig} />
        </>
    );
};

const container = document.getElementById('root');
const root = createRoot(container!);
root.render(<React.StrictMode><App /></React.StrictMode>);