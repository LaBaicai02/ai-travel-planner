import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowRight, Compass, MapPin, Sparkles, CalendarDays, Users, Wallet, Clock3, RotateCcw, LoaderCircle, AlertCircle, Leaf, Check, Route } from 'lucide-react'

import type { Form, Health, Plan } from './types'
import { currencyLabel, generatePlan, readHealth, safeSourceUrl } from './api'

const interests = ['自然风光', '历史文化', '地道美食', '艺术展览', '城市漫步', '休闲购物']
const today = () => { const date = new Date(); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10) }
const money = (value: string | null, currency: string) => value === null ? '费用不完整' : new Intl.NumberFormat('zh-CN', { style: 'currency', currency, maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(Number(value))
const costLabel = { reference: '来源参考价', estimated: '估算', unknown: '价格未知／待核实', demo: '模拟费用' }


export default function App() {
  const [form, setForm] = useState<Form>({ destination: '', departure_date: today(), days: '3', travelers: '2', budget: '3000', currency: 'CNY', interests: ['自然风光', '地道美食'] })
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState('')
  const [checkingHealth, setCheckingHealth] = useState(false)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const resultRef = useRef<HTMLElement>(null)
  async function refreshHealth() {
    setCheckingHealth(true)
    setHealthError('')
    try {
      const status = await readHealth()
      setHealth(status)
      setForm(current => ({ ...current, currency: status.supported_currencies.includes(current.currency) ? current.currency : status.supported_currencies[0] }))
    } catch (err) {
      setHealth(null)
      setHealthError(err instanceof Error ? err.message : '服务状态读取失败。')
    } finally { setCheckingHealth(false) }
  }
  useEffect(() => { void refreshHealth() }, [])
  const modeLabel = health ? health.mode === 'demo' ? '演示数据' : '真实模式 · 尚未接入' : '服务状态未知'
  function update(key: keyof Form, value: string) { setForm(current => ({ ...current, [key]: value })); setFieldErrors(current => ({ ...current, [key]: '' })); setError('') }
  async function generate(event?: FormEvent) {
    event?.preventDefault()
    if (loading || !health?.ready) return
    const errors: Record<string, string> = {}
    if (!form.destination.trim() || form.destination.trim().length > 60) errors.destination = '请输入 1–60 个字符的目的地'
    if (!form.departure_date || form.departure_date < '2000-01-01' || form.departure_date > '2100-12-18') errors.departure_date = '请选择 2000 年至 2100 年 12 月 18 日之间的日期'
    for (const [key, max, label] of [['days', 14, '天数'], ['travelers', 20, '人数']] as const) {
      if (!Number.isInteger(Number(form[key])) || Number(form[key]) < 1 || Number(form[key]) > max) errors[key] = label + '须为 1–' + max + ' 的整数'
    }
    if (!Number.isFinite(Number(form.budget)) || Number(form.budget) <= 0 || Number(form.budget) > 10000000 || !/^\d+(\.\d{1,2})?$/.test(form.budget)) errors.budget = '预算须大于 0，不超过 1000 万，最多两位小数'
    if (!form.interests.length) errors.interests = '请至少选择一项兴趣偏好'
    setFieldErrors(errors); setError('')
    if (Object.keys(errors).length) return
    setLoading(true)
    try {
      const data = await generatePlan({ ...form, destination: form.destination.trim(), days: Number(form.days), travelers: Number(form.travelers), budget: form.budget }, health.mode)
      setPlan(data)
      window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80)
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成失败，请重试。')
    } finally { setLoading(false) }
  }
  const fieldError = (name: string) => fieldErrors[name] ? <span id={name + '-error'} className="field-error">{fieldErrors[name]}</span> : null
  const inputProps = (name: keyof Form) => ({ 'aria-invalid': !!fieldErrors[name], 'aria-describedby': fieldErrors[name] ? name + '-error' : undefined })
  return (
    <div className="app">
      <header className="header"><a href="/" className="brand"><span className="brand-icon"><Compass size={23} /></span>漫游<span className="brand-divider" /><span className="brand-sub">AI 旅行规划助手</span></a><span className="demo-pill"><span />{modeLabel}</span></header>
      <main>
        <section className="hero">
          <div className="hero-copy"><div className="eyebrow"><Leaf size={15} /> 把时间留给旅途</div><h1>下一站，<br />遇见<span>你的理想旅程。</span></h1><p>告诉我们你想去哪里，剩下的交给漫游。<br />从第一缕晨光到城市夜色，安排每一天的小美好。</p><div className="hero-tags"><span><Check size={14} /> 按你的喜好规划</span><span><Check size={14} /> 预算一目了然</span></div></div>
          <div className="journey-art" aria-hidden="true"><div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" /><span className="art-dot dot-one" /><span className="art-dot dot-two" /><div className="art-label"><MapPin size={16} /> 下一站 · 心之所向</div><div className="mountain mountain-back" /><div className="mountain mountain-front" /><div className="sun" /><div className="art-card"><span className="art-card-icon"><Route size={22} /></span><div><strong>每一程，都有新发现</strong><small>YOUR NEXT ADVENTURE</small></div></div><span className="art-caption">LET’S WANDER A LITTLE.</span></div>
        </section>
        <div className="workspace">
          <section className="planner">
            <div className="section-heading"><span className="section-icon"><Compass size={20} /></span><div><h2>从一个旅行想法开始</h2><p>几项简单选择，开启你的专属行程</p></div></div>
            {healthError && <div className="error-banner" role="alert"><div>服务状态未知：{healthError}<button type="button" disabled={checkingHealth} onClick={() => void refreshHealth()}>重新检查服务</button></div></div>}
            {health && !health.ready && <div className="error-banner" role="alert">{health.message}</div>}
            <form onSubmit={generate} noValidate>
              <fieldset disabled={loading || !health}>
                <div className="field"><label htmlFor="destination">目的地 <span>*</span></label><div className="input-wrap"><MapPin size={17} /><input id="destination" placeholder="想去哪里？例如：杭州" maxLength={60} value={form.destination} onChange={e => update('destination', e.target.value)} {...inputProps('destination')} /></div>{fieldError('destination')}<div className="quick-places">{['杭州', '成都', '京都'].map(city => <button key={city} type="button" onClick={() => update('destination', city)}>{city}<ArrowRight size={11} /></button>)}</div></div>
                <div className="field"><label htmlFor="departure_date">出发日期 <span>*</span></label><div className="input-wrap"><CalendarDays size={17} /><input id="departure_date" type="date" min="2000-01-01" max="2100-12-18" value={form.departure_date} onChange={e => update('departure_date', e.target.value)} {...inputProps('departure_date')} /></div>{fieldError('departure_date')}</div>
                <div className="field-row"><div className="field"><label htmlFor="days">旅行天数 <span>*</span></label><div className="input-wrap"><Clock3 size={17} /><input id="days" type="number" min="1" max="14" step="1" value={form.days} onChange={e => update('days', e.target.value)} {...inputProps('days')} /><span>天</span></div>{fieldError('days')}</div><div className="field"><label htmlFor="travelers">出行人数 <span>*</span></label><div className="input-wrap"><Users size={17} /><input id="travelers" type="number" min="1" max="20" step="1" value={form.travelers} onChange={e => update('travelers', e.target.value)} {...inputProps('travelers')} /><span>人</span></div>{fieldError('travelers')}</div></div>
                <div className="field"><label htmlFor="budget">活动总预算 <span>*</span></label><div className="budget-row"><div className="input-wrap"><Wallet size={17} /><input id="budget" type="number" min="0.01" max="10000000" step="0.01" value={form.budget} onChange={e => update('budget', e.target.value)} {...inputProps('budget')} /></div><select aria-label="币种" value={form.currency} onChange={e => update('currency', e.target.value)}>{(health?.supported_currencies ?? []).map(currency => <option key={currency} value={currency}>{currencyLabel(currency)}</option>)}</select></div>{fieldError('budget')}<small className="helper">所有旅客合计 · 不含往返交通与住宿</small></div>
                <div className="field"><label>兴趣偏好 <span>*</span><small>可多选</small></label><div className="interest-grid" role="group" aria-label="兴趣偏好">{interests.map(interest => <button key={interest} type="button" className={form.interests.includes(interest) ? 'interest selected' : 'interest'} aria-pressed={form.interests.includes(interest)} onClick={() => { setForm(current => ({ ...current, interests: current.interests.includes(interest) ? current.interests.filter(x => x !== interest) : [...current.interests, interest] })); setFieldErrors(current => ({ ...current, interests: '' })) }}>{interest}{form.interests.includes(interest) && <Check size={13} />}</button>)}</div>{fieldError('interests')}</div>
                <button className="generate" type="submit" disabled={!health?.ready}>{loading ? <LoaderCircle size={18} className="spin" /> : <Sparkles size={18} />}{loading ? '正在编排你的旅程…' : plan ? '重新生成行程' : '生成我的行程'}{!loading && <ArrowRight size={17} />}</button>
              </fieldset>
              {error && <div className="error-banner" role="alert"><AlertCircle size={18} /><div>{error}<button type="button" disabled={loading || !health?.ready} onClick={() => generate()}>重试生成</button></div></div>}
              <p className="form-disclaimer">{health?.mode === 'demo' ? '演示数据 · 本阶段使用模拟行程，未接入 AI' : health?.message ?? '正在检查服务状态…'}</p>
            </form>
          </section>
          <section className="results" ref={resultRef} aria-live="polite" aria-busy={loading}>
            {loading ? <div className="empty-state loading-state"><div className="empty-icon"><LoaderCircle size={38} className="spin" /></div><span className="eyebrow">让期待慢慢成形</span><h2>正在编排你的旅程</h2><p>结合目的地、预算和兴趣，为你整理每日安排。</p><div className="skeleton" /><div className="skeleton short" /></div> : plan ? <>
              <div className="result-heading"><div><span className="eyebrow">YOUR TRAVEL PLAN · {plan.demo ? '演示数据' : '真实行程'}</span><h2>{plan.destination}，出发吧！</h2><p>{plan.days.length} 天 · {plan.travelers} 位旅客 · {plan.days[0].date} 出发</p></div><button type="button" className="regenerate" onClick={() => generate()} disabled={loading}><RotateCcw size={15} />重新生成</button></div>
              <div className="cost-summary"><div><Wallet size={21} /><span>预计活动总费用<small>所有旅客合计</small></span></div><strong>{money(plan.total_cost, plan.currency)}</strong></div>
              {plan.total_cost === null && <p className="known-cost">已知费用小计：{money(plan.known_cost_subtotal, plan.currency)}，不代表完整预算。</p>}
              {error && <p className="stale-result">上次成功结果 · 本次重新生成失败，以下行程未更新。</p>}
              <p className="result-notice">{plan.notice}</p>
              {plan.warnings.length > 0 && <ul className="plan-warnings">{plan.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
              {plan.days.map(day => <article className="day-card" key={day.day}><div className="day-heading"><span className="day-number">{String(day.day).padStart(2, '0')}</span><div><h3>第 {day.day} 天 · {day.title}</h3><span>{day.date}</span></div><strong>{money(day.estimated_cost, plan.currency)}<small>{day.estimated_cost === null ? '已知小计 ' + money(day.known_cost_subtotal, plan.currency) : '当天合计'}</small></strong></div><div className="activities">{day.activities.map((activity, index) => <div className="activity" key={index}><span className="timeline-dot" /><div className="activity-time"><Clock3 size={12} />{activity.time}</div><div className="activity-info"><h4>{activity.location}</h4><p>{activity.description}</p><span className="activity-cost">{costLabel[activity.cost_status]}{activity.estimated_cost !== null && ' ' + money(activity.estimated_cost, plan.currency)} · 全体旅客</span>
                {activity.cost_basis && <p className="cost-basis">{activity.cost_basis}</p>}
                {!plan.demo && <p className="opening-hours">建议安排时间见上方 · 营业时间：{activity.opening_hours_status === 'reported' ? activity.opening_hours + '（来源记载，出行前核实）' : '未知／待核实'}</p>}
                {activity.sources.length > 0 && <ul className="source-links">{activity.sources.filter(source => safeSourceUrl(source.url)).map(source => <li key={source.id}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a><small>检索时间：{source.retrieved_at}</small></li>)}</ul>}</div></div>)}</div></article>)}
            </> : <div className="empty-state"><div className="empty-illustration"><div className="empty-icon"><MapPin size={43} strokeWidth={1.5} /></div><span className="tiny-star">✧</span><span className="tiny-circle" /></div><span className="eyebrow">A JOURNEY BEGINS WITH AN IDEA</span><h2>你的下一段故事，从这里开始</h2><p>填写旅行信息，点击生成。<br />一份按天安排的旅行计划，就会出现在这里。</p><div className="empty-steps"><span><b>01</b>告诉我们想法</span><span className="step-line" /><span><b>02</b>收获每日行程</span></div><div className="empty-note"><Leaf size={17} /><span>留一点空白，让旅途里的惊喜自然发生。</span></div></div>}
          </section>
        </div>
      </main><footer><span><Compass size={15} />漫游 · 让每一次出发更轻松</span><span>{health?.mode === 'demo' ? '演示数据，仅供体验 · 真实出行前请核实' : '旅行信息请以官方最新公告为准'}</span></footer>
    </div>
  )
}
