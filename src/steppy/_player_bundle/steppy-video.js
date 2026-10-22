(function(Rt){"use strict";const $t={pre_action_ms:200,action_ms:300,post_action_ms:150,travel_speed_px_per_ms:1.5};function Ut(i,t,e){const s=i.captures[t]?.[e]?.content;return s||{title:i.title?.[t]??null,text:i.text[t]??"",subtitle:i.subtitle?.[t]??null}}/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const lt=globalThis,_t=lt.ShadowRoot&&(lt.ShadyCSS===void 0||lt.ShadyCSS.nativeShadow)&&"adoptedStyleSheets"in Document.prototype&&"replace"in CSSStyleSheet.prototype,bt=Symbol(),It=new WeakMap;let Ht=class{constructor(t,e,s){if(this._$cssResult$=!0,s!==bt)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=t,this.t=e}get styleSheet(){let t=this.o;const e=this.t;if(_t&&t===void 0){const s=e!==void 0&&e.length===1;s&&(t=It.get(e)),t===void 0&&((this.o=t=new CSSStyleSheet).replaceSync(this.cssText),s&&It.set(e,t))}return t}toString(){return this.cssText}};const Nt=i=>new Ht(typeof i=="string"?i:i+"",void 0,bt),be=(i,...t)=>{const e=i.length===1?i[0]:t.reduce((s,r,n)=>s+(o=>{if(o._$cssResult$===!0)return o.cssText;if(typeof o=="number")return o;throw Error("Value passed to 'css' function must be a 'css' function result: "+o+". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.")})(r)+i[n+1],i[0]);return new Ht(e,i,bt)},we=(i,t)=>{if(_t)i.adoptedStyleSheets=t.map(e=>e instanceof CSSStyleSheet?e:e.styleSheet);else for(const e of t){const s=document.createElement("style"),r=lt.litNonce;r!==void 0&&s.setAttribute("nonce",r),s.textContent=e.cssText,i.appendChild(s)}},Dt=_t?i=>i:i=>i instanceof CSSStyleSheet?(t=>{let e="";for(const s of t.cssRules)e+=s.cssText;return Nt(e)})(i):i;/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const{is:Ae,defineProperty:Ee,getOwnPropertyDescriptor:Se,getOwnPropertyNames:Me,getOwnPropertySymbols:ke,getPrototypeOf:Ce}=Object,ht=globalThis,Bt=ht.trustedTypes,Pe=Bt?Bt.emptyScript:"",Te=ht.reactiveElementPolyfillSupport,Y=(i,t)=>i,wt={toAttribute(i,t){switch(t){case Boolean:i=i?Pe:null;break;case Object:case Array:i=i==null?i:JSON.stringify(i)}return i},fromAttribute(i,t){let e=i;switch(t){case Boolean:e=i!==null;break;case Number:e=i===null?null:Number(i);break;case Object:case Array:try{e=JSON.parse(i)}catch{e=null}}return e}},Wt=(i,t)=>!Ae(i,t),Vt={attribute:!0,type:String,converter:wt,reflect:!1,useDefault:!1,hasChanged:Wt};Symbol.metadata??=Symbol("metadata"),ht.litPropertyMetadata??=new WeakMap;let V=class extends HTMLElement{static addInitializer(t){this._$Ei(),(this.l??=[]).push(t)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(t,e=Vt){if(e.state&&(e.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(t)&&((e=Object.create(e)).wrapped=!0),this.elementProperties.set(t,e),!e.noAccessor){const s=Symbol(),r=this.getPropertyDescriptor(t,s,e);r!==void 0&&Ee(this.prototype,t,r)}}static getPropertyDescriptor(t,e,s){const{get:r,set:n}=Se(this.prototype,t)??{get(){return this[e]},set(o){this[e]=o}};return{get:r,set(o){const c=r?.call(this);n?.call(this,o),this.requestUpdate(t,c,s)},configurable:!0,enumerable:!0}}static getPropertyOptions(t){return this.elementProperties.get(t)??Vt}static _$Ei(){if(this.hasOwnProperty(Y("elementProperties")))return;const t=Ce(this);t.finalize(),t.l!==void 0&&(this.l=[...t.l]),this.elementProperties=new Map(t.elementProperties)}static finalize(){if(this.hasOwnProperty(Y("finalized")))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(Y("properties"))){const e=this.properties,s=[...Me(e),...ke(e)];for(const r of s)this.createProperty(r,e[r])}const t=this[Symbol.metadata];if(t!==null){const e=litPropertyMetadata.get(t);if(e!==void 0)for(const[s,r]of e)this.elementProperties.set(s,r)}this._$Eh=new Map;for(const[e,s]of this.elementProperties){const r=this._$Eu(e,s);r!==void 0&&this._$Eh.set(r,e)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(t){const e=[];if(Array.isArray(t)){const s=new Set(t.flat(1/0).reverse());for(const r of s)e.unshift(Dt(r))}else t!==void 0&&e.push(Dt(t));return e}static _$Eu(t,e){const s=e.attribute;return s===!1?void 0:typeof s=="string"?s:typeof t=="string"?t.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){this._$ES=new Promise(t=>this.enableUpdating=t),this._$AL=new Map,this._$E_(),this.requestUpdate(),this.constructor.l?.forEach(t=>t(this))}addController(t){(this._$EO??=new Set).add(t),this.renderRoot!==void 0&&this.isConnected&&t.hostConnected?.()}removeController(t){this._$EO?.delete(t)}_$E_(){const t=new Map,e=this.constructor.elementProperties;for(const s of e.keys())this.hasOwnProperty(s)&&(t.set(s,this[s]),delete this[s]);t.size>0&&(this._$Ep=t)}createRenderRoot(){const t=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return we(t,this.constructor.elementStyles),t}connectedCallback(){this.renderRoot??=this.createRenderRoot(),this.enableUpdating(!0),this._$EO?.forEach(t=>t.hostConnected?.())}enableUpdating(t){}disconnectedCallback(){this._$EO?.forEach(t=>t.hostDisconnected?.())}attributeChangedCallback(t,e,s){this._$AK(t,s)}_$ET(t,e){const s=this.constructor.elementProperties.get(t),r=this.constructor._$Eu(t,s);if(r!==void 0&&s.reflect===!0){const n=(s.converter?.toAttribute!==void 0?s.converter:wt).toAttribute(e,s.type);this._$Em=t,n==null?this.removeAttribute(r):this.setAttribute(r,n),this._$Em=null}}_$AK(t,e){const s=this.constructor,r=s._$Eh.get(t);if(r!==void 0&&this._$Em!==r){const n=s.getPropertyOptions(r),o=typeof n.converter=="function"?{fromAttribute:n.converter}:n.converter?.fromAttribute!==void 0?n.converter:wt;this._$Em=r;const c=o.fromAttribute(e,n.type);this[r]=c??this._$Ej?.get(r)??c,this._$Em=null}}requestUpdate(t,e,s,r=!1,n){if(t!==void 0){const o=this.constructor;if(r===!1&&(n=this[t]),s??=o.getPropertyOptions(t),!((s.hasChanged??Wt)(n,e)||s.useDefault&&s.reflect&&n===this._$Ej?.get(t)&&!this.hasAttribute(o._$Eu(t,s))))return;this.C(t,e,s)}this.isUpdatePending===!1&&(this._$ES=this._$EP())}C(t,e,{useDefault:s,reflect:r,wrapped:n},o){s&&!(this._$Ej??=new Map).has(t)&&(this._$Ej.set(t,o??e??this[t]),n!==!0||o!==void 0)||(this._$AL.has(t)||(this.hasUpdated||s||(e=void 0),this._$AL.set(t,e)),r===!0&&this._$Em!==t&&(this._$Eq??=new Set).add(t))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(e){Promise.reject(e)}const t=this.scheduleUpdate();return t!=null&&await t,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??=this.createRenderRoot(),this._$Ep){for(const[r,n]of this._$Ep)this[r]=n;this._$Ep=void 0}const s=this.constructor.elementProperties;if(s.size>0)for(const[r,n]of s){const{wrapped:o}=n,c=this[r];o!==!0||this._$AL.has(r)||c===void 0||this.C(r,void 0,n,c)}}let t=!1;const e=this._$AL;try{t=this.shouldUpdate(e),t?(this.willUpdate(e),this._$EO?.forEach(s=>s.hostUpdate?.()),this.update(e)):this._$EM()}catch(s){throw t=!1,this._$EM(),s}t&&this._$AE(e)}willUpdate(t){}_$AE(t){this._$EO?.forEach(e=>e.hostUpdated?.()),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(t)),this.updated(t)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(t){return!0}update(t){this._$Eq&&=this._$Eq.forEach(e=>this._$ET(e,this[e])),this._$EM()}updated(t){}firstUpdated(t){}};V.elementStyles=[],V.shadowRootOptions={mode:"open"},V[Y("elementProperties")]=new Map,V[Y("finalized")]=new Map,Te?.({ReactiveElement:V}),(ht.reactiveElementVersions??=[]).push("2.1.2");/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const At=globalThis,jt=i=>i,dt=At.trustedTypes,Ft=dt?dt.createPolicy("lit-html",{createHTML:i=>i}):void 0,qt="$lit$",C=`lit$${Math.random().toFixed(9).slice(2)}$`,Kt="?"+C,Oe=`<${Kt}>`,z=document,G=()=>z.createComment(""),Z=i=>i===null||typeof i!="object"&&typeof i!="function",Et=Array.isArray,ze=i=>Et(i)||typeof i?.[Symbol.iterator]=="function",St=`[ 	
\f\r]`,J=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,Xt=/-->/g,Yt=/>/g,L=RegExp(`>|${St}(?:([^\\s"'>=/]+)(${St}*=${St}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`,"g"),Gt=/'/g,Zt=/"/g,Jt=/^(?:script|style|textarea|title)$/i,j=Symbol.for("lit-noChange"),v=Symbol.for("lit-nothing"),Qt=new WeakMap,R=z.createTreeWalker(z,129);function te(i,t){if(!Et(i)||!i.hasOwnProperty("raw"))throw Error("invalid template strings array");return Ft!==void 0?Ft.createHTML(t):t}const Le=(i,t)=>{const e=i.length-1,s=[];let r,n=t===2?"<svg>":t===3?"<math>":"",o=J;for(let c=0;c<e;c++){const p=i[c];let h,d,a=-1,y=0;for(;y<p.length&&(o.lastIndex=y,d=o.exec(p),d!==null);)y=o.lastIndex,o===J?d[1]==="!--"?o=Xt:d[1]!==void 0?o=Yt:d[2]!==void 0?(Jt.test(d[2])&&(r=RegExp("</"+d[2],"g")),o=L):d[3]!==void 0&&(o=L):o===L?d[0]===">"?(o=r??J,a=-1):d[1]===void 0?a=-2:(a=o.lastIndex-d[2].length,h=d[1],o=d[3]===void 0?L:d[3]==='"'?Zt:Gt):o===Zt||o===Gt?o=L:o===Xt||o===Yt?o=J:(o=L,r=void 0);const u=o===L&&i[c+1].startsWith("/>")?" ":"";n+=o===J?p+Oe:a>=0?(s.push(h),p.slice(0,a)+qt+p.slice(a)+C+u):p+C+(a===-2?c:u)}return[te(i,n+(i[e]||"<?>")+(t===2?"</svg>":t===3?"</math>":"")),s]};class Q{constructor({strings:t,_$litType$:e},s){let r;this.parts=[];let n=0,o=0;const c=t.length-1,p=this.parts,[h,d]=Le(t,e);if(this.el=Q.createElement(h,s),R.currentNode=this.el.content,e===2||e===3){const a=this.el.content.firstChild;a.replaceWith(...a.childNodes)}for(;(r=R.nextNode())!==null&&p.length<c;){if(r.nodeType===1){if(r.hasAttributes())for(const a of r.getAttributeNames())if(a.endsWith(qt)){const y=d[o++],u=r.getAttribute(a).split(C),_=/([.?@])?(.*)/.exec(y);p.push({type:1,index:n,name:_[2],strings:u,ctor:_[1]==="."?Ue:_[1]==="?"?Ie:_[1]==="@"?He:ut}),r.removeAttribute(a)}else a.startsWith(C)&&(p.push({type:6,index:n}),r.removeAttribute(a));if(Jt.test(r.tagName)){const a=r.textContent.split(C),y=a.length-1;if(y>0){r.textContent=dt?dt.emptyScript:"";for(let u=0;u<y;u++)r.append(a[u],G()),R.nextNode(),p.push({type:2,index:++n});r.append(a[y],G())}}}else if(r.nodeType===8)if(r.data===Kt)p.push({type:2,index:n});else{let a=-1;for(;(a=r.data.indexOf(C,a+1))!==-1;)p.push({type:7,index:n}),a+=C.length-1}n++}}static createElement(t,e){const s=z.createElement("template");return s.innerHTML=t,s}}function F(i,t,e=i,s){if(t===j)return t;let r=s!==void 0?e._$Co?.[s]:e._$Cl;const n=Z(t)?void 0:t._$litDirective$;return r?.constructor!==n&&(r?._$AO?.(!1),n===void 0?r=void 0:(r=new n(i),r._$AT(i,e,s)),s!==void 0?(e._$Co??=[])[s]=r:e._$Cl=r),r!==void 0&&(t=F(i,r._$AS(i,t.values),r,s)),t}class Re{constructor(t,e){this._$AV=[],this._$AN=void 0,this._$AD=t,this._$AM=e}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(t){const{el:{content:e},parts:s}=this._$AD,r=(t?.creationScope??z).importNode(e,!0);R.currentNode=r;let n=R.nextNode(),o=0,c=0,p=s[0];for(;p!==void 0;){if(o===p.index){let h;p.type===2?h=new tt(n,n.nextSibling,this,t):p.type===1?h=new p.ctor(n,p.name,p.strings,this,t):p.type===6&&(h=new Ne(n,this,t)),this._$AV.push(h),p=s[++c]}o!==p?.index&&(n=R.nextNode(),o++)}return R.currentNode=z,r}p(t){let e=0;for(const s of this._$AV)s!==void 0&&(s.strings!==void 0?(s._$AI(t,s,e),e+=s.strings.length-2):s._$AI(t[e])),e++}}class tt{get _$AU(){return this._$AM?._$AU??this._$Cv}constructor(t,e,s,r){this.type=2,this._$AH=v,this._$AN=void 0,this._$AA=t,this._$AB=e,this._$AM=s,this.options=r,this._$Cv=r?.isConnected??!0}get parentNode(){let t=this._$AA.parentNode;const e=this._$AM;return e!==void 0&&t?.nodeType===11&&(t=e.parentNode),t}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(t,e=this){t=F(this,t,e),Z(t)?t===v||t==null||t===""?(this._$AH!==v&&this._$AR(),this._$AH=v):t!==this._$AH&&t!==j&&this._(t):t._$litType$!==void 0?this.$(t):t.nodeType!==void 0?this.T(t):ze(t)?this.k(t):this._(t)}O(t){return this._$AA.parentNode.insertBefore(t,this._$AB)}T(t){this._$AH!==t&&(this._$AR(),this._$AH=this.O(t))}_(t){this._$AH!==v&&Z(this._$AH)?this._$AA.nextSibling.data=t:this.T(z.createTextNode(t)),this._$AH=t}$(t){const{values:e,_$litType$:s}=t,r=typeof s=="number"?this._$AC(t):(s.el===void 0&&(s.el=Q.createElement(te(s.h,s.h[0]),this.options)),s);if(this._$AH?._$AD===r)this._$AH.p(e);else{const n=new Re(r,this),o=n.u(this.options);n.p(e),this.T(o),this._$AH=n}}_$AC(t){let e=Qt.get(t.strings);return e===void 0&&Qt.set(t.strings,e=new Q(t)),e}k(t){Et(this._$AH)||(this._$AH=[],this._$AR());const e=this._$AH;let s,r=0;for(const n of t)r===e.length?e.push(s=new tt(this.O(G()),this.O(G()),this,this.options)):s=e[r],s._$AI(n),r++;r<e.length&&(this._$AR(s&&s._$AB.nextSibling,r),e.length=r)}_$AR(t=this._$AA.nextSibling,e){for(this._$AP?.(!1,!0,e);t!==this._$AB;){const s=jt(t).nextSibling;jt(t).remove(),t=s}}setConnected(t){this._$AM===void 0&&(this._$Cv=t,this._$AP?.(t))}}class ut{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(t,e,s,r,n){this.type=1,this._$AH=v,this._$AN=void 0,this.element=t,this.name=e,this._$AM=r,this.options=n,s.length>2||s[0]!==""||s[1]!==""?(this._$AH=Array(s.length-1).fill(new String),this.strings=s):this._$AH=v}_$AI(t,e=this,s,r){const n=this.strings;let o=!1;if(n===void 0)t=F(this,t,e,0),o=!Z(t)||t!==this._$AH&&t!==j,o&&(this._$AH=t);else{const c=t;let p,h;for(t=n[0],p=0;p<n.length-1;p++)h=F(this,c[s+p],e,p),h===j&&(h=this._$AH[p]),o||=!Z(h)||h!==this._$AH[p],h===v?t=v:t!==v&&(t+=(h??"")+n[p+1]),this._$AH[p]=h}o&&!r&&this.j(t)}j(t){t===v?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,t??"")}}class Ue extends ut{constructor(){super(...arguments),this.type=3}j(t){this.element[this.name]=t===v?void 0:t}}class Ie extends ut{constructor(){super(...arguments),this.type=4}j(t){this.element.toggleAttribute(this.name,!!t&&t!==v)}}class He extends ut{constructor(t,e,s,r,n){super(t,e,s,r,n),this.type=5}_$AI(t,e=this){if((t=F(this,t,e,0)??v)===j)return;const s=this._$AH,r=t===v&&s!==v||t.capture!==s.capture||t.once!==s.once||t.passive!==s.passive,n=t!==v&&(s===v||r);r&&this.element.removeEventListener(this.name,this,s),n&&this.element.addEventListener(this.name,this,t),this._$AH=t}handleEvent(t){typeof this._$AH=="function"?this._$AH.call(this.options?.host??this.element,t):this._$AH.handleEvent(t)}}class Ne{constructor(t,e,s){this.element=t,this.type=6,this._$AN=void 0,this._$AM=e,this.options=s}get _$AU(){return this._$AM._$AU}_$AI(t){F(this,t)}}const De=At.litHtmlPolyfillSupport;De?.(Q,tt),(At.litHtmlVersions??=[]).push("3.3.3");const Be=(i,t,e)=>{const s=e?.renderBefore??t;let r=s._$litPart$;if(r===void 0){const n=e?.renderBefore??null;s._$litPart$=r=new tt(t.insertBefore(G(),n),n,void 0,e??{})}return r._$AI(i),r};/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const Mt=globalThis;class ft extends V{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){const t=super.createRenderRoot();return this.renderOptions.renderBefore??=t.firstChild,t}update(t){const e=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(t),this._$Do=Be(e,this.renderRoot,this.renderOptions)}connectedCallback(){super.connectedCallback(),this._$Do?.setConnected(!0)}disconnectedCallback(){super.disconnectedCallback(),this._$Do?.setConnected(!1)}render(){return j}}ft._$litElement$=!0,ft.finalized=!0,Mt.litElementHydrateSupport?.({LitElement:ft});const We=Mt.litElementPolyfillSupport;We?.({LitElement:ft}),(Mt.litElementVersions??=[]).push("4.2.2");const M={bg:"#f4f1ea",surface:"#fbf9f4",chromeBg:"#efece3",fg:"#1f1d1a",muted:"#8a857c",border:"#e1ddd1",accent:"#d24f2c",accentWeak:"rgba(210, 79, 44, 0.35)",success:"#3a7d44"},kt={serif:"ui-serif, Georgia, 'Times New Roman', serif",sans:"system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",mono:"ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"},q={padding:"8px 12px",radius:"6px",maxWidth:"280px",fontSize:"14px",lineHeight:"1.4",shadow:"0 4px 12px rgba(0, 0, 0, 0.2)"},ee={radius:"4px",padding:"4px 10px"},m=i=>Nt(i),Ve=be`
  :host {
    display: block;
    position: relative;
    /* palette */
    --steppy-bg: ${m(M.bg)};
    --steppy-surface: ${m(M.surface)};
    --steppy-chrome-bg: ${m(M.chromeBg)};
    --steppy-fg: ${m(M.fg)};
    --steppy-muted: ${m(M.muted)};
    --steppy-border: ${m(M.border)};
    --steppy-accent: ${m(M.accent)};
    --steppy-accent-weak: ${m(M.accentWeak)};
    --steppy-success: ${m(M.success)};
    /* typography */
    --steppy-font-serif: ${m(kt.serif)};
    --steppy-font-sans: ${m(kt.sans)};
    --steppy-font-mono: ${m(kt.mono)};
    --steppy-font-family: var(--steppy-font-sans);
    /* engine vars (names preserved, re-pointed at the palette) */
    --steppy-cursor-size: 24px;
    --steppy-cursor-color: var(--steppy-fg);
    --steppy-ripple-color: rgba(31, 29, 26, 0.25);
    --steppy-hotspot-color: var(--steppy-accent);
    --steppy-hotspot-glow: var(--steppy-accent-weak);
    --steppy-tooltip-bg: var(--steppy-fg);
    --steppy-tooltip-fg: var(--steppy-surface);

    font-family: var(--steppy-font-family);
    color: var(--steppy-fg);
  }

  .steppy-wrapper {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  /* ── Stage ─────────────────────────────────────────────────────────── */
  .steppy-stage {
    position: relative;
    width: 100%;
    background: var(--steppy-chrome-bg);
    overflow: hidden;
    border-radius: 8px;
    user-select: none;
  }
  .steppy-stage img {
    display: block;
    /* width:auto caps display at the screenshot's intrinsic pixel width (never
       upscales), max-width:100% fills/shrinks within the stage. A dense capture
       (mobile device_scale_factor=3) downscales to fill the stage crisply; a
       low-res capture renders at native size instead of blurred. The img stays
       left-aligned (no centering) so the cursor/hotspot overlay — positioned in
       img-display-space with the stage top-left as origin — stays aligned. */
    width: auto;
    max-width: 100%;
    height: auto;
    pointer-events: none;
  }
  .steppy-cursor {
    position: absolute;
    top: 0;
    left: 0;
    width: var(--steppy-cursor-size);
    height: var(--steppy-cursor-size);
    pointer-events: none;
    opacity: 0;
    transition: opacity 200ms ease-in-out;
    background: var(--steppy-cursor-color);
    clip-path: polygon(0 0, 0 75%, 25% 55%, 45% 95%, 60% 85%, 40% 50%, 75% 40%);
    filter: drop-shadow(0 2px 6px rgba(0, 0, 0, 0.3));
    transform-origin: 0 0;
    z-index: 4;
  }
  .steppy-cursor.visible {
    opacity: 1;
  }
  .steppy-cursor.pulse {
    animation: steppy-pulse 200ms ease-in-out;
  }
  @keyframes steppy-pulse {
    0%,
    100% {
      transform: scale(1);
    }
    50% {
      transform: scale(1.25);
    }
  }
  .steppy-ripple {
    position: absolute;
    top: 0;
    left: 0;
    width: 12px;
    height: 12px;
    margin: -6px;
    border-radius: 50%;
    background: transparent;
    border: 2px solid var(--steppy-ripple-color);
    pointer-events: none;
    opacity: 0;
    z-index: 3;
  }
  .steppy-ripple.fire {
    animation: steppy-ripple 400ms ease-out forwards;
  }
  @keyframes steppy-ripple {
    0% {
      transform: scale(0.6);
      opacity: 1;
    }
    100% {
      transform: scale(3);
      opacity: 0;
    }
  }
  .steppy-kbd {
    position: absolute;
    top: 0;
    left: 0;
    background: var(--steppy-fg);
    color: var(--steppy-surface);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
    font-family: var(--steppy-font-mono);
    opacity: 0;
    transition: opacity 120ms;
    pointer-events: none;
    z-index: 5;
  }
  .steppy-kbd.visible {
    opacity: 1;
  }
  .steppy-hotspot {
    position: absolute;
    box-sizing: border-box;
    background: transparent;
    border: 2px solid var(--steppy-hotspot-color);
    border-radius: 4px;
    cursor: pointer;
    padding: 0;
    z-index: 2;
    transition: outline-offset 120ms;
    animation: steppy-hotspot-pulse 1500ms ease-in-out infinite;
    box-shadow: 0 0 0 4px var(--steppy-hotspot-glow);
  }
  .steppy-hotspot:focus-visible {
    outline: 3px solid var(--steppy-hotspot-color);
    outline-offset: 4px;
  }
  .steppy-hotspot.miss {
    animation: steppy-hotspot-shake 400ms;
  }
  @keyframes steppy-hotspot-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 4px var(--steppy-hotspot-glow);
    }
    50% {
      box-shadow: 0 0 0 10px transparent;
    }
  }
  @keyframes steppy-hotspot-shake {
    0%,
    100% {
      transform: translateX(0);
    }
    25% {
      transform: translateX(-6px);
    }
    75% {
      transform: translateX(6px);
    }
  }
  .steppy-tooltip {
    position: absolute;
    background: var(--steppy-tooltip-bg);
    color: var(--steppy-tooltip-fg);
    padding: ${m(q.padding)};
    border-radius: ${m(q.radius)};
    max-width: ${m(q.maxWidth)};
    font-size: ${m(q.fontSize)};
    line-height: ${m(q.lineHeight)};
    z-index: 6;
    pointer-events: none;
    box-shadow: ${m(q.shadow)};
  }
  .steppy-miss-toast {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    bottom: 16px;
    background: var(--steppy-fg);
    color: var(--steppy-surface);
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 13px;
    z-index: 7;
    pointer-events: none;
    animation: steppy-toast 1400ms ease-out forwards;
  }
  @keyframes steppy-toast {
    0%,
    80% {
      opacity: 1;
    }
    100% {
      opacity: 0;
    }
  }
  .steppy-stage-click-catcher {
    position: absolute;
    inset: 0;
    z-index: 1;
    cursor: default;
  }

  /* ── Status / error ────────────────────────────────────────────────── */
  .steppy-state {
    padding: 24px;
    text-align: center;
    color: var(--steppy-muted);
  }
  .steppy-state.error {
    color: #b3402a;
    background: #fbeae6;
    border-radius: 6px;
  }
  .steppy-state .steppy-state-detail {
    font-size: 12px;
    margin-top: 8px;
    font-family: var(--steppy-font-mono);
    white-space: pre-wrap;
    text-align: left;
    max-height: 200px;
    overflow: auto;
  }

  /* ── Shared chrome controls ────────────────────────────────────────── */
  .steppy-chrome {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    background: var(--steppy-chrome-bg);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 14px;
  }
  .steppy-progress {
    margin-right: auto;
    font-family: var(--steppy-font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--steppy-muted);
  }
  .steppy-btn {
    font: inherit;
    background: var(--steppy-surface);
    border: 1px solid var(--steppy-border);
    border-radius: ${m(ee.radius)};
    padding: ${m(ee.padding)};
    cursor: pointer;
    color: var(--steppy-fg);
  }
  .steppy-btn:hover:not(:disabled) {
    border-color: var(--steppy-muted);
  }
  .steppy-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .steppy-field {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--steppy-muted);
  }
  .steppy-field select {
    font: inherit;
    background: var(--steppy-surface);
    border: 1px solid var(--steppy-border);
    border-radius: 4px;
    padding: 4px 8px;
    cursor: pointer;
    color: var(--steppy-fg);
  }
  /* Segmented control (pill container; active = dark fill + light text). */
  .steppy-seg {
    display: inline-flex;
    background: var(--steppy-bg);
    border: 1px solid var(--steppy-border);
    border-radius: 999px;
    padding: 2px;
    gap: 2px;
  }
  .steppy-seg-btn {
    font: inherit;
    font-size: 13px;
    border: 0;
    background: transparent;
    color: var(--steppy-muted);
    padding: 3px 12px;
    border-radius: 999px;
    cursor: pointer;
  }
  .steppy-seg-btn[aria-current='true'] {
    background: var(--steppy-fg);
    color: var(--steppy-surface);
  }
  .steppy-seg-btn:focus-visible {
    outline: 2px solid var(--steppy-accent);
    outline-offset: 1px;
  }

  @media (prefers-reduced-motion: reduce) {
    .steppy-cursor,
    .steppy-cursor.pulse,
    .steppy-ripple.fire,
    .steppy-hotspot,
    .steppy-hotspot.miss,
    .steppy-miss-toast {
      animation: none !important;
      transition: none !important;
    }
  }
`,U=i=>new Promise(t=>setTimeout(t,Math.max(0,i)));function se(i){return i*i*(3-2*i)}function ie(i,t){if(!t||!t.width||!t.height)return null;const e=i.getBoundingClientRect();return!e.width||!e.height?null:{sx:e.width/t.width,sy:e.height/t.height}}function Ct(i,t){return{x:i.x*t.sx,y:i.y*t.sy,width:i.width*t.sx,height:i.height*t.sy}}function re(i){return{x:i.x+i.width/2,y:i.y+i.height/2}}function ne(i,t,e){if(!i)return null;const s=ie(t,e);return s?re(Ct(i,s)):null}function oe(i,t,e){const s=t.x-i.x,r=t.y-i.y,n=Math.hypot(s,r);return Math.max(150,n/(e||1.5))}function ae(i,t){const e=t.x-i.x,s=t.y-i.y,r=Math.hypot(e,s);if(!r)return{x:i.x,y:i.y};const n=(i.x+t.x)/2,o=(i.y+t.y)/2,c=-s/r,p=e/r;return{x:n+c*r*.15,y:o+p*r*.15}}class et{constructor(t){this.cursorEl=null,this.rippleEl=null,this.kbdEl=null,this.imgEl=null,this.currentPos={x:8,y:8},this.aborted=!1,this.stage=t.stage,this.timings={...$t,...t.timings??{}},this.onPhaseChange=t.onPhaseChange,this.refreshDom()}setTimings(t){this.timings={...this.timings,...t}}setStage(t){this.stage=t,this.refreshDom()}abort(){this.aborted=!0}hide(){this.cursorEl&&this.cursorEl.classList.remove("visible"),this.kbdEl&&this.kbdEl.classList.remove("visible"),this.onPhaseChange?.("idle")}refreshDom(){this.cursorEl=this.stage.querySelector(".steppy-cursor"),this.rippleEl=this.stage.querySelector(".steppy-ripple"),this.kbdEl=this.stage.querySelector(".steppy-kbd"),this.imgEl=this.stage.querySelector("img")}static normalizeTargets(t){if(!t)return[];if(t.kind==="type-multi")return t.targets.map(r=>({bbox:r.bbox,value:r.value,selector:r.selector,kind:"type"}));const e=t.target,s=t.kind==="type"?t.value:void 0;return[{bbox:e.bbox,value:s,selector:e.selector,kind:t.kind==="click"?"click":t.kind==="hover"?"hover":"type"}]}async waitImageReady(){const t=this.imgEl;t&&(t.complete&&t.naturalWidth||await new Promise(e=>{const s=()=>e();t.addEventListener("load",s,{once:!0}),t.addEventListener("error",s,{once:!0})}))}async animateFrame(t){if(this.aborted=!1,this.refreshDom(),!this.cursorEl)return;if(!t.cursor){this.hide();return}await this.waitImageReady();const e=et.normalizeTargets(t.cursor);this.currentPos={x:8,y:8},this.cursorEl.classList.add("visible");for(let s=0;s<e.length;s++){if(this.aborted||(await this.travelToTargetIndex(t,e,s),this.aborted)||(await this.playTargetActionIndex(t,e,s),this.aborted))return;await U(this.timings.post_action_ms)}}async travelToTarget(t,e){if(this.aborted=!1,this.refreshDom(),!this.cursorEl)return;if(!t.cursor){this.hide();return}await this.waitImageReady();const s=et.normalizeTargets(t.cursor);this.cursorEl.classList.add("visible"),await this.travelToTargetIndex(t,s,e)}async playTargetAction(t,e){if(this.aborted=!1,this.refreshDom(),!t.cursor)return;const s=et.normalizeTargets(t.cursor);await this.playTargetActionIndex(t,s,e),!this.aborted&&await U(this.timings.post_action_ms)}computeDisplayRect(t,e){const s=this.imgEl;if(!s||!t)return null;const r=ie(s,e);return r?Ct(t,r):null}async travelToTargetIndex(t,e,s){const r=e[s],n=this.cursorEl;if(!r||!n)return;const o=ne(r.bbox,this.imgEl,t.viewport);o&&(this.onPhaseChange?.("travel",r),await this.travelTo(n,this.currentPos,o),this.currentPos=o,this.onPhaseChange?.("pre-action",r),n.classList.remove("pulse"),n.offsetWidth,n.classList.add("pulse"),await U(this.timings.pre_action_ms),n.classList.remove("pulse"))}async playTargetActionIndex(t,e,s){const r=e[s];if(!r)return;const n=ne(r.bbox,this.imgEl,t.viewport)||this.currentPos;this.onPhaseChange?.("action",r),r.kind==="click"?(this.rippleEl&&this.fireClick(this.rippleEl,n),await U(this.timings.action_ms)):r.kind==="type"?this.kbdEl&&await this.typeValue(this.kbdEl,n,r.value):await U(this.timings.action_ms),this.onPhaseChange?.("post-action",r)}async travelTo(t,e,s){const r=oe(e,s,this.timings.travel_speed_px_per_ms),n=ae(e,s);return new Promise(o=>{const c=performance.now(),p=h=>{if(this.aborted){o();return}const d=Math.min(1,(h-c)/r),a=se(d),y=(1-a)*(1-a)*e.x+2*(1-a)*a*n.x+a*a*s.x,u=(1-a)*(1-a)*e.y+2*(1-a)*a*n.y+a*a*s.y;t.style.left=`${y}px`,t.style.top=`${u}px`,d<1?requestAnimationFrame(p):o()};requestAnimationFrame(p)})}fireClick(t,e){t.style.left=`${e.x}px`,t.style.top=`${e.y}px`,t.classList.remove("fire"),t.offsetWidth,t.classList.add("fire")}async typeValue(t,e,s){t.style.left=`${e.x+16}px`,t.style.top=`${e.y+16}px`,t.textContent="",t.classList.add("visible");const r=(s??"").split(""),n=r.length?this.timings.action_ms/r.length:this.timings.action_ms;for(const o of r){if(this.aborted)break;t.textContent=(t.textContent??"")+o,await U(n)}await U(80),t.classList.remove("visible")}}const je=6,Fe=1e-7;function Pt(i,t,e){const s=3*t,r=3*(e-t)-s;return(((1-s-r)*i+r)*i+s)*i}function qe(i,t,e){const s=3*t,r=3*(e-t)-s;return(3*(1-s-r)*i+2*r)*i+s}function pe(i,t,e,s){return i===t&&e===s?r=>st(r):r=>{const n=st(r);if(n===0||n===1)return n;let o=n;for(let c=0;c<je;c++){const p=qe(o,i,e);if(p===0)break;o-=(Pt(o,i,e)-n)/p}if(!(o>=0&&o<=1)){let c=0,p=1;for(o=n;p-c>Fe;)Pt(o,i,e)<n?c=o:p=o,o=(c+p)/2}return Pt(o,t,s)}}function st(i){return i>0?i<1?i:1:0}const Tt=pe(.42,0,.58,1),Ke=pe(0,0,.58,1);function yt(i,t,e){return i+(t-i)*st(e)}const Xe="_",ce={x:8,y:8},le=400,Ye=.6,Ge=3,he=200,de=1.25,ue=200,fe=80,ye=16,ge=Math.round,it={holdMs:700,minStepMs:1500,narrationPadMs:400,introMs:1200,outroMs:1500};function Ze(i,t){return re(Ct(i.bbox,t))}function Je(i,t){return i.kind!=="type"?t.action_ms:(i.value??"").length?t.action_ms+fe:fe}function Ot(i,t,e){return i.captures?.[t]?.[e]?.frames??[]}function me(i,t){return t||(i.layouts.length>0?i.layouts[0]:Xe)}function Qe(i,t){const{locale:e,scale:s,fps:r}=t,n=t.holdMs??it.holdMs,o=t.minStepMs??it.minStepMs,c=t.narrationPadMs??it.narrationPadMs,p=t.introMs??it.introMs,h=t.outroMs??it.outroMs,d=t.narrationMs??{},a={...$t,...i.cursor},y=me(i,t.layout),u=[],_=[],k=[],K=[],nt=[];let I={...ce},A=!1,f=0;p>0&&(u.push({phase:"intro",startMs:0,endMs:p,stepIndex:-1,frameIndex:-1,targetIndex:-1}),f=p),i.steps.forEach((E,S)=>{const P=Ot(E,e,y);nt.push(P.map(B=>B.screenshot));const b=f,H=P.some(B=>B.cursor!==null);A!==H&&(K.push({atMs:b,visible:H}),A=H),P.forEach((B,W)=>{const l=et.normalizeTargets(B.cursor);if(l.length===0){u.push({phase:"hold",startMs:f,endMs:f+n,stepIndex:S,frameIndex:W,targetIndex:-1}),f+=n;return}l.forEach((g,x)=>{const w=Ze(g,s),O=I,at=ge(oe(O,w,a.travel_speed_px_per_ms));u.push({phase:"travel",startMs:f,endMs:f+at,stepIndex:S,frameIndex:W,targetIndex:x,kind:g.kind,from:O,to:w,control:ae(O,w)}),f+=at,I=w,u.push({phase:"pre-action",startMs:f,endMs:f+a.pre_action_ms,stepIndex:S,frameIndex:W,targetIndex:x,kind:g.kind,to:w}),f+=a.pre_action_ms;const pt=Je(g,a);g.kind==="click"&&k.push({atMs:f,at:w}),u.push({phase:"action",startMs:f,endMs:f+pt,stepIndex:S,frameIndex:W,targetIndex:x,kind:g.kind,to:w,value:g.value}),f+=pt,u.push({phase:"post-action",startMs:f,endMs:f+a.post_action_ms,stepIndex:S,frameIndex:W,targetIndex:x,kind:g.kind,to:w}),f+=a.post_action_ms})});const T=f-b,N=d[E.id]??0,D=ge(Math.max(T+Math.max(n,E.waitAfter),o,N>0?N+c:0)),mt=Math.max(0,P.length-1);u.push({phase:"hold",startMs:f,endMs:b+D,stepIndex:S,frameIndex:mt,targetIndex:-1}),f=b+D;const ot=Ut(E,e,y);_.push({stepId:E.id,stepIndex:S,startMs:b,endMs:f,text:ot.text,title:ot.title||ot.text})}),A&&(K.push({atMs:f,visible:!1}),A=!1),h>0&&(u.push({phase:"outro",startMs:f,endMs:f+h,stepIndex:-1,frameIndex:-1,targetIndex:-1}),f+=h);const gt=f;return{durationMs:gt,frameCount:Math.max(1,Math.ceil(gt*r/1e3)),fps:r,segments:u,cues:_,ripples:k,visibility:K,screenshots:nt,stepCount:i.steps.length,locale:e,layoutKey:y}}function ts(i,t){return t*1e3/i.fps}function es(i,t){if(i.length===0)return null;let e=0,s=i.length-1,r=i[0];for(;e<=s;){const n=e+s>>1,o=i[n];o.startMs<=t?(r=o,e=n+1):s=n-1}return r}function ss(i,t){let e=!1,s=-1/0;for(const c of i.visibility){if(c.atMs>t)break;e=c.visible,s=c.atMs}const r=e?1:0,n=e?0:1,o=t-s;return!Number.isFinite(s)||o>=ue?r:yt(n,r,Tt(o/ue))}function is(i){if(i<0||i>=he)return 1;const t=he/2;return i<t?yt(1,de,Tt(i/t)):yt(de,1,Tt((i-t)/t))}function rs(i,t){let e=null;for(const n of i.ripples){if(n.atMs>t)break;e=n}if(!e)return null;const s=t-e.atMs;if(s>=le)return null;const r=Ke(s/le);return{x:e.at.x,y:e.at.y,scale:yt(Ye,Ge,r),opacity:1-r}}function ns(i,t,e){if(!i)return"";const s=e/i.length;if(s<=0)return i;const r=Math.min(i.length,Math.floor(t/s)+1);return i.slice(0,Math.max(0,r))}function os(i,t,e,s){const r=1-s;return{x:r*r*i.x+2*r*s*t.x+s*s*e.x,y:r*r*i.y+2*r*s*t.y+s*s*e.y}}function as(i,t,e){const s=es(i.segments,t);if(!s||s.phase==="intro"||s.phase==="outro"){const a=s?.phase==="outro";return{timeMs:t,stepIndex:-1,frameIndex:-1,screenshot:"",cursor:null,ripple:null,kbd:null,caption:"",captionTitle:"",progress:a?1:0,card:a?"outro":"intro"}}const r=i.stepCount>0?st((s.stepIndex+1)/i.stepCount):0,n=i.cues[s.stepIndex],o=i.screenshots[s.stepIndex]?.[Math.max(0,s.frameIndex)]??"",c=t-s.startMs;let p=null;const h=ss(i,t);if(h>0){let a,y=1;if(s.phase==="travel"&&s.from&&s.to&&s.control){const u=s.endMs-s.startMs;a=os(s.from,s.control,s.to,se(st(u>0?c/u:1)))}else s.to?(a=s.to,s.phase==="pre-action"&&(y=is(c))):a=ps(i,t);p={x:a.x,y:a.y,opacity:h,scale:y}}let d=null;if(s.phase==="action"&&s.kind==="type"&&s.to){const a=ns(s.value??"",c,e.action_ms);a&&(d={x:s.to.x+ye,y:s.to.y+ye,text:a})}return{timeMs:t,stepIndex:s.stepIndex,frameIndex:Math.max(0,s.frameIndex),screenshot:o,cursor:p,ripple:rs(i,t),kbd:d,caption:n?.text??"",captionTitle:n?.title??"",progress:r,card:null}}function ps(i,t){for(let e=i.segments.length-1;e>=0;e--){const s=i.segments[e];if(s.startMs<=t&&s.to)return s.to}return{...ce}}function cs(i,t,e){return as(i,ts(i,t),e)}function zt(i,t){return i===t?!0:!i||!t?!1:Math.round(i.x)===Math.round(t.x)&&Math.round(i.y)===Math.round(t.y)}function xe(i,t){return Math.round(i*100)===Math.round(t*100)}function ls(i,t){return!(i.card!==t.card||i.screenshot!==t.screenshot||i.caption!==t.caption||i.captionTitle!==t.captionTitle||Math.round(i.progress*1e3)!==Math.round(t.progress*1e3)||!!i.cursor!=!!t.cursor||i.cursor&&t.cursor&&(!zt(i.cursor,t.cursor)||!xe(i.cursor.opacity,t.cursor.opacity)||Math.round(i.cursor.scale*100)!==Math.round(t.cursor.scale*100))||!!i.ripple!=!!t.ripple||i.ripple&&t.ripple&&(!zt(i.ripple,t.ripple)||!xe(i.ripple.opacity,t.ripple.opacity)||Math.round(i.ripple.scale*100)!==Math.round(t.ripple.scale*100))||!!i.kbd!=!!t.kbd||i.kbd&&t.kbd&&(i.kbd.text!==t.kbd.text||!zt(i.kbd,t.kbd)))}function ve(i){const t=Math.max(2,Math.floor(i));return t%2===0?t:t-1}const hs=`
  :host {
    display: block;
    position: relative;
    overflow: hidden;
    background: var(--steppy-bg);
    --steppy-video-band-pad: 20px;
  }
  /* The live player animates these with CSS keyframes and transitions, both
     wall-clock driven. The timeline supplies the same values per frame. */
  .steppy-stage img,
  .steppy-cursor,
  .steppy-ripple,
  .steppy-kbd {
    animation: none !important;
    transition: none !important;
  }
  .steppy-video-frame {
    position: relative;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    background: var(--steppy-bg);
  }
  .steppy-stage {
    position: relative;
    overflow: hidden;
    background: var(--steppy-chrome-bg);
  }
  .steppy-stage img {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: auto;
    display: block;
    opacity: 0;
    max-width: none;
  }
  .steppy-stage img.on { opacity: 1; }
  .steppy-cursor, .steppy-ripple, .steppy-kbd { opacity: 0; }

  .steppy-video-band {
    box-sizing: border-box;
    padding: var(--steppy-video-band-pad);
    background: var(--steppy-chrome-bg);
    border-top: 1px solid var(--steppy-border);
    display: flex;
    flex-direction: column;
    gap: 12px;
    justify-content: center;
  }
  .steppy-video-progress {
    height: 4px;
    border-radius: 999px;
    background: var(--steppy-border);
    overflow: hidden;
  }
  .steppy-video-progress > i {
    display: block;
    height: 100%;
    width: 0%;
    background: var(--steppy-accent);
  }
  .steppy-video-caption {
    font-family: var(--steppy-font-sans);
    font-size: 20px;
    line-height: 1.45;
    color: var(--steppy-fg);
    margin: 0;
  }
  .steppy-video-caption b { font-weight: 600; }

  .steppy-video-card {
    position: absolute;
    inset: 0;
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 18px;
    padding: 8%;
    text-align: center;
    background: var(--steppy-bg);
    z-index: 10;
  }
  .steppy-video-card.on { display: flex; }
  .steppy-video-card h1 {
    font-family: var(--steppy-font-serif);
    font-size: 44px;
    line-height: 1.15;
    margin: 0;
    color: var(--steppy-fg);
  }
  .steppy-video-card p {
    font-family: var(--steppy-font-sans);
    font-size: 22px;
    margin: 0;
    color: var(--steppy-muted);
  }
  .steppy-video-card img { max-width: 200px; max-height: 80px; }
  .steppy-video-card .rule {
    width: 72px;
    height: 4px;
    border-radius: 999px;
    background: var(--steppy-accent);
  }

  .steppy-video-watermark {
    position: absolute;
    right: 20px;
    top: 16px;
    max-height: 32px;
    opacity: 0.85;
    z-index: 8;
  }
  .steppy-video-stamp {
    position: absolute;
    right: 16px;
    bottom: 10px;
    font-family: var(--steppy-font-mono);
    font-size: 12px;
    color: var(--steppy-muted);
    z-index: 8;
  }
`;function $(i,t){const e=document.createElement(i);return t&&(e.className=t),e}function ds(i,t){return i.complete&&i.naturalWidth?Promise.resolve(!0):new Promise(e=>{let s=!1;const r=o=>{s||(s=!0,clearTimeout(n),e(o))},n=setTimeout(()=>r(!1),Math.max(0,t));i.addEventListener("load",()=>r(!0),{once:!0}),i.addEventListener("error",()=>r(!1),{once:!0})})}function $e(i,t,e){const s=e.captions??"band",r=me(t,e.layout),n={...$t,...t.cursor},o=i.shadowRoot??i.attachShadow({mode:"open"});o.innerHTML="";const c=$("style");c.textContent=Ve.cssText+hs,o.appendChild(c),e.accent&&i.style.setProperty("--steppy-accent",e.accent),e.fontFamily&&i.style.setProperty("--steppy-font-family",e.fontFamily);const p=$("div","steppy-video-frame"),h=$("div","steppy-stage");p.appendChild(h),o.appendChild(p);const d=new Map,a=[];for(const l of t.steps)for(const g of Ot(l,e.locale,r)){if(d.has(g.screenshot))continue;const x=$("img");x.src=g.screenshot,x.alt="",d.set(g.screenshot,x),a.push(g.screenshot),h.appendChild(x)}const y=$("div","steppy-cursor"),u=$("div","steppy-ripple"),_=$("div","steppy-kbd");h.append(y,u,_);const k=$("div","steppy-video-band"),K=$("div","steppy-video-progress"),nt=$("i");K.appendChild(nt);const I=$("div","steppy-video-caption");k.append(K,I),s!=="off"&&o.appendChild(k);const A=$("div","steppy-video-card"),f=$("h1"),gt=$("div","rule"),E=$("p"),S=$("img");e.logo&&(S.src=e.logo,A.appendChild(S)),A.append(f,gt,E),o.appendChild(A);let P=null;if(e.logo&&(P=$("img","steppy-video-watermark"),P.src=e.logo,o.appendChild(P)),e.stamp){const l=$("div","steppy-video-stamp");l.textContent=e.stamp,o.appendChild(l)}const b=ve(e.width);let H=b,T=null,N=null,D="",mt=[];function ot(){if(s==="off")return 0;let l=0;const g=t.steps.map(x=>Ut(x,e.locale,r).text);for(const x of g.length?g:[""])I.textContent=x,l=Math.max(l,k.scrollHeight||k.getBoundingClientRect().height||0);return I.textContent="",Math.ceil(l)||96}async function B(){const l=e.imageTimeoutMs??15e3,g=await Promise.all(a.map(ct=>ds(d.get(ct),l)));mt=a.filter((ct,gs)=>!g[gs]);const x=document.fonts;x?.ready&&await x.ready;const w=a.length?d.get(a[0]):null,O=us(t,e.locale,r),at=w?.naturalWidth||O.width,pt=w?.naturalHeight||O.height;let xt=b,X=Math.round(pt*xt/at);const vt=ot(),Lt=e.maxHeight??null;if(Lt&&X+vt>Lt){const ct=Math.max(1,Lt-vt);xt=Math.max(2,Math.round(at*ct/pt)),X=ct}h.style.width=`${xt}px`,h.style.height=`${X}px`,p.style.width=`${b}px`,p.style.height=`${X}px`,s!=="off"&&(k.style.width=`${b}px`,k.style.height=`${vt}px`),H=ve(X+vt),i.style.width=`${b}px`,i.style.height=`${H}px`;const fs=xt/O.width,ys=X/O.height;T=Qe(t,{...e,scale:{sx:fs,sy:ys}})}function W(l){if(l.card){A.classList.add("on");const g=l.card==="outro";f.textContent=e.title??"",E.textContent=(g?e.outroText:e.subtitle)??"",E.style.display=E.textContent?"":"none"}else A.classList.remove("on");l.screenshot!==D&&(D&&d.get(D)?.classList.remove("on"),d.get(l.screenshot)?.classList.add("on"),D=l.screenshot),l.cursor?(y.style.opacity=String(l.cursor.opacity),y.style.left=`${l.cursor.x}px`,y.style.top=`${l.cursor.y}px`,y.style.transform=`scale(${l.cursor.scale})`):y.style.opacity="0",l.ripple?(u.style.opacity=String(l.ripple.opacity),u.style.left=`${l.ripple.x}px`,u.style.top=`${l.ripple.y}px`,u.style.transform=`scale(${l.ripple.scale})`):u.style.opacity="0",l.kbd?(_.style.opacity="1",_.style.left=`${l.kbd.x}px`,_.style.top=`${l.kbd.y}px`,_.textContent=l.kbd.text):(_.style.opacity="0",_.textContent=""),I.textContent=l.caption,nt.style.width=`${Math.round(l.progress*1e3)/10}%`}return{get timeline(){return rt(T)},get durationMs(){return rt(T).durationMs},get frameCount(){return rt(T).frameCount},ready:B,size:()=>({width:b,height:H}),cues:()=>rt(T).cues,missingShots:()=>[...mt],seek(l){const g=rt(T),x=cs(g,l,n);return N&&ls(N,x)?!1:(W(x),N=x,!0)},destroy(){o.innerHTML="",N=null}}}function rt(i){if(!i)throw new Error("steppy-video: await ready() before using the stage");return i}function us(i,t,e){let s=null;for(const r of i.steps)for(const n of Ot(r,t,e)){if(!s){s=n.captureSize;continue}if(n.captureSize.width!==s.width||n.captureSize.height!==s.height)throw new Error(`steppy-video: step ${r.index} was captured at ${n.captureSize.width}x${n.captureSize.height} but the guide starts at ${s.width}x${s.height}. A video needs one fixed stage — recapture this guide (steppy run --guide) so every screenshot shares a viewport.`)}if(!s)throw new Error(`steppy-video: no captures for locale '${t}' / layout '${e}'.`);return s}const _e={stage:null,mount(i,t,e){const s=e??document.getElementById("steppy-video")??document.body,r=$e(s,i,t);return _e.stage=r,r}};window.__STEPPY_VIDEO__=_e,Rt.mountVideoStage=$e,Object.defineProperty(Rt,Symbol.toStringTag,{value:"Module"})})(this.__steppyVideo=this.__steppyVideo||{});
//# sourceMappingURL=steppy-video.js.map
