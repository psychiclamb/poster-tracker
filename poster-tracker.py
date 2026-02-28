from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import streamlit as st
from sqlalchemy import text


# ===================== DB =====================
def get_conn():
    # Streamlit Cloud -> Settings -> Secrets: DB_URL = "postgresql+psycopg2://..."
    return st.connection("postgresql", type="sql", url=st.secrets["DB_URL"])


# ===================== Config =====================
# Sadece dikey poster var
VARIANTS: List[Tuple[str, str]] = [
    ("dikey", "Dikey"),
]

# Telif analizi editten önce
COLUMN_STEPS: List[Tuple[str, str]] = [
    ("telif_analizi_yapildi", "Telif analizi yapıldı"),
    ("posterler_editlendi", "Posterler editlendi"),
    ("kalite_artirildi", "Kalite artırıldı"),
    ("urun_aciklamalari_olusturuldu", "Ürün açıklamaları oluşturuldu"),
    ("mockuplar_videolar_olusturuldu", "Mockuplar ve videolar oluşturuldu"),
    ("printify_yuklendi", "Printify'a yüklendi"),
    ("etsy_yuklendi", "Etsy'e yüklendi"),
]

# Bu kısım artık yok (UI'da gösterilmeyecek).
# DB şemasını bozmamak için boş bırakıyoruz.
GLOBAL_STEPS: List[Tuple[str, str]] = []


# ===================== Utils =====================
def force_rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def toast(msg: str) -> None:
    if hasattr(st, "toast"):
        st.toast(msg)
    else:
        st.success(msg)


def norm(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def checkbox_key(item_id: str, variant_key: Optional[str], step_key: str) -> str:
    # global yok ama geriye uyumluluk için fonksiyonu tutuyoruz
    if variant_key is None:
        return f"{item_id}__global__{step_key}"
    return f"{item_id}__{variant_key}__{step_key}"


def empty_variant_steps() -> Dict[str, bool]:
    return {k: False for k, _ in COLUMN_STEPS}


def ensure_checkbox_state(key: str, default_val: bool) -> None:
    if key not in st.session_state:
        st.session_state[key] = default_val


def set_item_all_session_state(item_id: str, value: bool) -> None:
    for vk, _ in VARIANTS:
        for sk, _ in COLUMN_STEPS:
            st.session_state[checkbox_key(item_id, vk, sk)] = value


def bump_sort_key() -> None:
    st.session_state["item_sort_key_v"] = int(st.session_state.get("item_sort_key_v", 0)) + 1


def _safe_json_to_dict(x) -> Dict:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


# ===================== Model =====================
@dataclass
class TopicProgress:
    id: str
    label: str
    order: int
    # global_steps artık kullanılmıyor ama DB geriye uyum için tutuyoruz
    global_steps: Dict[str, bool]
    variants: Dict[str, Dict[str, bool]]

    @staticmethod
    def new(label: str, order: int) -> "TopicProgress":
        item_id = uuid.uuid4().hex
        variants = {vk: empty_variant_steps() for vk, _ in VARIANTS}
        return TopicProgress(
            id=item_id,
            label=label.strip(),
            order=order,
            global_steps={},  # kullanılmıyor
            variants=variants,
        )


# ===================== DB CRUD =====================
def load_data() -> Dict[str, TopicProgress]:
    conn = get_conn()
    data: Dict[str, TopicProgress] = {}

    with conn.session as session:
        rows = session.execute(
            text("select id, label, order_num, global_steps, variants from artist_progress")
        ).mappings().all()

    for r in rows:
        item_id = str(r["id"]).strip()
        label = str(r["label"]).strip()
        order = int(r["order_num"])

        # global kullanılmıyor; yine de okuyup dict'e çeviriyoruz (boş olabilir)
        g_in = _safe_json_to_dict(r["global_steps"])
        v_in = _safe_json_to_dict(r["variants"])

        variants: Dict[str, Dict[str, bool]] = {}
        for vk, _ in VARIANTS:
            steps_in = v_in.get(vk, {})
            if not isinstance(steps_in, dict):
                steps_in = {}
            steps = empty_variant_steps()
            for sk, _ in COLUMN_STEPS:
                steps[sk] = bool(steps_in.get(sk, False))
            variants[vk] = steps

        data[item_id] = TopicProgress(
            id=item_id,
            label=label,
            order=order,
            global_steps={k: bool(g_in.get(k, False)) for k in g_in.keys()},
            variants=variants,
        )

    return data


def save_data(data: Dict[str, TopicProgress]) -> None:
    conn = get_conn()

    upsert_sql = text("""
        insert into artist_progress (id, label, order_num, global_steps, variants, updated_at)
        values (:id, :label, :order_num, cast(:global_steps as jsonb), cast(:variants as jsonb), now())
        on conflict (id) do update set
            label = excluded.label,
            order_num = excluded.order_num,
            global_steps = excluded.global_steps,
            variants = excluded.variants,
            updated_at = now()
    """)

    with conn.session as session:
        for ap in data.values():
            session.execute(
                upsert_sql,
                {
                    "id": ap.id,
                    "label": ap.label,
                    "order_num": ap.order,
                    "global_steps": json.dumps({}, ensure_ascii=False),  # kullanılmıyor
                    "variants": json.dumps(ap.variants, ensure_ascii=False),
                },
            )
        session.commit()


def delete_item_db(item_id: str) -> None:
    conn = get_conn()
    with conn.session as session:
        session.execute(text("delete from artist_progress where id = :id"), {"id": item_id})
        session.commit()


def truncate_all_db() -> None:
    conn = get_conn()
    with conn.session as session:
        session.execute(text("truncate table artist_progress"))
        session.commit()


# ===================== Logic =====================
def calc_done_total(ap: TopicProgress) -> Tuple[int, int]:
    done = 0
    total = 0
    for vk, _ in VARIANTS:
        for sk, _ in COLUMN_STEPS:
            total += 1
            if ap.variants.get(vk, {}).get(sk, False):
                done += 1
    return done, total


def apply_order_from_id_list(data: Dict[str, TopicProgress], ordered_ids: List[str]) -> bool:
    seen = set()
    new_list: List[str] = []
    for i in ordered_ids:
        if i in data and i not in seen:
            seen.add(i)
            new_list.append(i)
    for i in data.keys():
        if i not in seen:
            new_list.append(i)

    changed = False
    for idx, item_id in enumerate(new_list, start=1):
        if data[item_id].order != idx:
            data[item_id].order = idx
            changed = True

    if changed:
        save_data(data)
    return changed


# ===================== Sortables (optional) =====================
SORTABLES_OK = False
sort_items = None
try:
    from streamlit_sortables import sort_items as _sort_items  # pip install streamlit-sortables
    sort_items = _sort_items
    SORTABLES_OK = True
except Exception:
    SORTABLES_OK = False


# ===================== UI =====================
st.set_page_config(page_title="Poster Upload Süreç Takibi", layout="wide")
st.title("🖼️ Poster Upload Süreç Takibi")

if "item_sort_key_v" not in st.session_state:
    st.session_state["item_sort_key_v"] = 0

data = load_data()

with st.sidebar:
    st.header("➕ Konu başlığı ekle")
    with st.form("add_item_form", clear_on_submit=True):
        new_name = st.text_input("Konu başlığı", placeholder="Örn: Minimalist Travel Posters")
        submitted = st.form_submit_button("Ekle", use_container_width=True)

    if submitted:
        name = (new_name or "").strip()
        if not name:
            st.warning("İsim boş olamaz.")
        else:
            if any(norm(ap.label) == norm(name) for ap in data.values()):
                st.warning("Bu konu başlığı zaten listede var.")
            else:
                max_order = max((ap.order for ap in data.values()), default=0)
                ap = TopicProgress.new(label=name, order=max_order + 1)
                data[ap.id] = ap
                save_data(data)
                bump_sort_key()
                toast("Eklendi ✅")
                force_rerun()

    st.divider()
    st.header("↕️ Sıralama")

    if not data:
        st.info("Liste boş. Önce konu başlığı ekle.")
    else:
        ordered = sorted(data.values(), key=lambda a: a.order)
        ordered_ids = [a.id for a in ordered]

        if SORTABLES_OK:
            st.caption("Sürükle-bırak ile sırala:")
            sort_key = f"item_sort_{st.session_state['item_sort_key_v']}"

            display = [f"{a.label}  ⟦{a.id[:8]}⟧" for a in ordered]
            display_to_id = {f"{a.label}  ⟦{a.id[:8]}⟧": a.id for a in ordered}

            try:
                new_display = sort_items(display, direction="vertical", key=sort_key)
                new_ids = [display_to_id[x] for x in new_display if x in display_to_id]

                if new_ids and new_ids != ordered_ids:
                    changed = apply_order_from_id_list(data, new_ids)
                    if changed:
                        toast("Sıra güncellendi ✅")
                        bump_sort_key()
                        force_rerun()
            except Exception:
                st.warning("Drag&drop çalışmadı. Aşağıdaki ↑ ↓ ile sırala.")
                SORTABLES_OK = False

        if not SORTABLES_OK:
            st.caption("↑ ↓ ile sırala (drag&drop için: pip install streamlit-sortables)")
            for i, ap in enumerate(ordered):
                c1, c2, c3 = st.columns([6, 1, 1])
                with c1:
                    st.write(ap.label)
                with c2:
                    if st.button("↑", key=f"up_{ap.id}", disabled=(i == 0)):
                        above = ordered[i - 1]
                        ap.order, above.order = above.order, ap.order
                        save_data(data)
                        toast("Sıra güncellendi ✅")
                        force_rerun()
                with c3:
                    if st.button("↓", key=f"down_{ap.id}", disabled=(i == len(ordered) - 1)):
                        below = ordered[i + 1]
                        ap.order, below.order = below.order, ap.order
                        save_data(data)
                        toast("Sıra güncellendi ✅")
                        force_rerun()

    st.divider()
    st.header("🔎 Filtre / Sıralama")
    q = st.text_input("Ara", placeholder="travel", key="search_q")
    filter_mode = st.selectbox(
        "Göster",
        ["Hepsi", "Sadece tamamlanmamışlar", "Sadece tamamlanmışlar"],
        index=0,
        key="filter_mode",
    )
    sort_mode = st.selectbox(
        "Liste görünümü sırası",
        ["Liste sırası", "Başlık (A→Z)", "İlerleme (çok→az)"],
        index=0,
        key="sort_mode",
    )

    st.divider()
    if st.button("🧨 Her şeyi sıfırla (DB)", use_container_width=True, key="btn_reset_all"):
        truncate_all_db()
        st.success("Sıfırlandı.")
        st.stop()


# ======= Main list =======
items = list(data.values())

if q.strip():
    qq = q.strip().lower()
    items = [a for a in items if qq in a.label.lower()]

if filter_mode != "Hepsi":
    if filter_mode == "Sadece tamamlanmamışlar":
        items = [a for a in items if calc_done_total(a)[0] < calc_done_total(a)[1]]
    else:
        items = [a for a in items if calc_done_total(a)[0] == calc_done_total(a)[1]]

if sort_mode == "Liste sırası":
    items.sort(key=lambda a: a.order)
elif sort_mode == "Başlık (A→Z)":
    items.sort(key=lambda a: a.label.lower())
else:
    items.sort(key=lambda a: calc_done_total(a)[0] / max(1, calc_done_total(a)[1]), reverse=True)

overall_done = 0
overall_total = 0
for a in items:
    d, t = calc_done_total(a)
    overall_done += d
    overall_total += t

st.progress(0 if overall_total == 0 else overall_done / overall_total)
st.caption(f"Genel ilerleme: {overall_done}/{overall_total} adım tamamlandı")
st.markdown("---")

if not items:
    st.info("Liste boş. Soldan konu başlığı ekleyebilirsin.")
    st.stop()

for ap in items:
    done, total = calc_done_total(ap)
    pct = 0 if total == 0 else done / total
    item_id = ap.id

    with st.container(border=True):
        top_l, top_m, top_r = st.columns([3, 2, 2])

        with top_l:
            st.subheader(ap.label)

        with top_m:
            st.progress(pct)
            st.caption(f"{int(pct*100)}% ({done}/{total})")

        with top_r:
            b1, b2, b3, b4 = st.columns([1, 1, 1, 1])

            with b1:
                if st.button("Hepsi ✅", key=f"btn_all_{item_id}"):
                    for vk, _ in VARIANTS:
                        ap.variants[vk] = {sk: True for sk, _ in COLUMN_STEPS}
                    data[item_id] = ap
                    save_data(data)
                    set_item_all_session_state(item_id, True)
                    force_rerun()

            with b2:
                if st.button("Hepsi ⬜", key=f"btn_none_{item_id}"):
                    for vk, _ in VARIANTS:
                        ap.variants[vk] = {sk: False for sk, _ in COLUMN_STEPS}
                    data[item_id] = ap
                    save_data(data)
                    set_item_all_session_state(item_id, False)
                    force_rerun()

            with b3:
                if st.button("Sıfırla", key=f"btn_reset_{item_id}"):
                    for vk, _ in VARIANTS:
                        ap.variants[vk] = {sk: False for sk, _ in COLUMN_STEPS}
                    data[item_id] = ap
                    save_data(data)
                    set_item_all_session_state(item_id, False)
                    force_rerun()

            with b4:
                del_flag = st.session_state.get(f"del_confirm_{item_id}", False)
                if not del_flag:
                    if st.button("🗑", key=f"btn_del_{item_id}"):
                        st.session_state[f"del_confirm_{item_id}"] = True
                        force_rerun()
                else:
                    if st.button("Onayla", key=f"btn_del_ok_{item_id}"):
                        for vk, _ in VARIANTS:
                            for sk, _ in COLUMN_STEPS:
                                st.session_state.pop(checkbox_key(item_id, vk, sk), None)

                        delete_item_db(item_id)
                        data.pop(item_id, None)

                        bump_sort_key()
                        st.session_state.pop(f"del_confirm_{item_id}", None)
                        toast("Silindi 🗑️")
                        force_rerun()

                    if st.button("Vazgeç", key=f"btn_del_cancel_{item_id}"):
                        st.session_state.pop(f"del_confirm_{item_id}", None)
                        force_rerun()

        st.markdown("**Poster (Dikey):**")
        changed = False

        # Tek varyant: dikey
        vk = "dikey"
        for sk, slabel in COLUMN_STEPS:
            k = checkbox_key(item_id, vk, sk)
            ensure_checkbox_state(k, ap.variants.get(vk, {}).get(sk, False))
            nv = st.checkbox(slabel, key=k)
            if nv != ap.variants[vk].get(sk, False):
                ap.variants[vk][sk] = nv
                changed = True

        if changed:
            data[item_id] = ap
            save_data(data)
