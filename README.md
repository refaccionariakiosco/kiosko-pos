# Kiosco POS

Punto de venta (POS) de escritorio para kioscos, offline-first, construido en
Python con PySide6/Qt y SQLite. Su lógica sigue una arquitectura en capas con
CQRS (domain → application → infrastructure → interface).

Es la reconstrucción de la PWA "Kiosco POS" como aplicación nativa de escritorio.

## Características

- **Vender**: búsqueda por nombre o código, cátalogo por categorías, carrito,
  cobro con vuelto (EFECTIVO / TARJETA / TRANSFERENCIA) y recibo imprimible.
- **Productos**: alta, edición, activación/desactivación e impresión de
  etiquetas de código de barras.
- **Stock**: ajustes manuales y movimientos con auditoría (VENTA, COMPRA,
  AJUSTE, ANULACION).
- **Historial**: consulta de ventas, detalle, reimpresión de recibo y anulación
  (restaura stock automáticamente).
- **Dashboard**: ventas e ingresos del día, alertas de stock bajo.
- Offline-first: toda la información vive en una base SQLite local. Sin
  conexión de red requerida.

## Arquitectura

- **domain**: entidades (Product, Sale, Category, StockMovement), Money
  (Decimal, sin errores de coma flotante), eventos de dominio y contratos de
  repositorios.
- **application**: CQRS con CommandBus/QueryBus, commands, queries, handlers y
  DTOs de lectura.
- **infrastructure**: SQLAlchemy + SQLite, Unit of Work (transacciones atómicas
  y despacho de eventos post-commit), generación de códigos EAN-13/CODE128,
  render de etiquetas y recibos, e impresión.
- **interface**: interfaz Qt (main window + vistas por módulo).

## Modelo de datos

Esquema SQLite/Supabase: el catálogo es global (`products`, `categories`), el
inventario físico es por sucursal (`inventory`) y las ventas, caja, apartados y
pedidos son operativos locales (replicables a la nube).

```mermaid
erDiagram
    SYS_CONFIG ||--o{ SYS_CONFIG : "clave/valor"

    CATEGORIES ||--o{ PRODUCTS : "clasifica"
    PRODUCTS ||--o{ INVENTORY : "existencias por sucursal"
    PRODUCTS ||--o{ STOCK_MOVEMENTS : "cardex"
    PRODUCTS ||--o{ SALE_ITEMS : "se vende"
    PRODUCTS ||--o{ APARTADO_ITEMS : "se reserva"
    PRODUCTS oo--o{ PROVIDER_ITEMS : "enlaza lista de precios"
    PRODUCTS oo--o{ PURCHASE_ORDER_LINES : "se recibe en pedidos"

    SALES ||--|{ SALE_ITEMS : "contiene"
    SALES ||--|{ SALE_PAYMENTS : "cobra"

    APARTADOS ||--|{ APARTADO_ITEMS : "retiene"
    APARTADOS ||--|{ APARTADO_ABONOS : "recibe abonos"

    CASH_DAYS ||--o{ CASH_MOVEMENTS : "jornada"

    PROVIDERS ||--o{ PROVIDER_ITEMS : "lista de precios"
    PURCHASE_ORDERS ||--|{ PURCHASE_ORDER_LINES : "detalla"
```

Tablas principales:

| Tabla                 | Contenido                                                            |
| --------------------- | -------------------------------------------------------------------- |
| `categories`          | Categorías de productos                                               |
| `products`            | Catálogo global (código, nombre, precios, espejo legado de stock)     |
| `inventory`           | Stock físico por sucursal `(product_id, branch_id)`                   |
| `stock_movements`     | Auditoría de movimientos (VENTA, COMPRA, AJUSTE, ANULACION, …)        |
| `sales` / `sale_items` / `sale_payments` | Venta, renglones y formas de pago                          |
| `apartados` / `apartado_items` / `apartado_abonos` | Apartados a clientes con abonos                      |
| `cash_days` / `cash_movements` | Jornada de caja (apertura/corte) y movimientos de efectivo      |
| `providers` / `provider_items` | Proveedores y sus listas de precios para cotizar                 |
| `purchase_orders` / `purchase_order_lines` | Pedidos a proveedor (pendiente/recibido/cancelado)     |
| `sys_config`          | Clave/valor: identidad de sucursal, terminal y credenciales de nube  |

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Desarrollo (tests):

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Uso

```bash
# Base por defecto en ./data/kiosco.db
python -m app.main

# Demo con datos de ejemplo
python -m app.main --demo

# Otras opciones
python -m app.main --db ruta\a\kiosco.db --log-level DEBUG
```

## Impresión de etiquetas (Brother QL-810W)

- Etiqueta configurada: **90 × 29 mm** (9 cm × 3 cm) a **300 dpi**.
- Los códigos de 13 dígitos se imprimen como **EAN-13**; el resto como
  **CODE128**.

Dos transportes disponibles:

1. **Driver de Windows (por defecto)**

   ```bash
   python -m app.main --printer windows
   ```

   Imprime con el driver de la Brother QL-810W instalado en el sistema
   (QPrinter). Opción más simple si la impresora ya está configurada en Windows.

2. **Red (brother_ql, opcional)**

   ```bash
   pip install brother-ql
   python -m app.main --printer brother_ql --brother-ip 192.168.1.50
   ```

   Envía la etiqueta 29x90 directamente a la impresora por red (puerto TCP
   9100), sin depender del driver de Windows.

En la vista **Productos**, cada fila tiene un botón "Imprimir etiqueta" que
abre el diálogo de impresión (copias, vista previa y selección de transporte).

## Importar inventario desde Excel

El sistema puede cargar un inventario existente desde un archivo `.xlsx`:

```bash
# Ver qué se importaría (sin tocar nada)
python -m app.inventory_import "app\total inventario.xlsx" --dry-run

# Importar de verdad (requiere una base vacía)
python -m app.inventory_import "app\total inventario.xlsx"

# A otra base o solo las primeras N filas
python -m app.inventory_import "inventario.xlsx" --db data\otra.db --limit 500
```

Mapeo de columnas del Excel (estándar de los reportes de inventario):

| Columna Excel        | Campo del producto               |
| -------------------- | -------------------------------- |
| Código               | código de barras (EAN-13/CODE128)|
| Producto             | nombre                           |
| P. Costo             | costo unitario                   |
| P. Venta             | precio de venta                  |
| P. Mayoreo           | precio mayorista (opcional)      |
| Departamento         | categoría (se crea al vuelo)     |
| Existencia           | stock inicial                    |
| Inv. Mínimo          | stock mínimo (alerta)            |

Los importes en formato `$1,234.50` o `1,234.50` se interpretan correctamente.
La columna `- Sin Departamento -` deja el producto sin categoría.

## Tests

```bash
python -m pytest tests -q
```

Cubre dominio (Money, entidades, stock), casos de uso (crear/actualizar
productos, ventas, anulaciones, ajustes), códigos de barras y un smoke test
de la interfaz Qt (offscreen).

## Arquitectura offline-first: catálogo vs inventario y topología (Fase 1)

El objetivo es evolucionar a una arquitectura offline-first **sin cambiar la
UI**: el catálogo (productos, precios) es global y se replica a todas las
instalaciones; el **inventario (stock físico) queda estrictamente vinculado a
una sucursal** (`id_sucursal`), de modo que las existencias de una sucursal
nunca se mezclan con las de otra.

- **Catálogo** → tabla `products` (nombre, código, precio base, categoría).
- **Inventario** → tabla `inventory` con `(product_id, branch_id, stock, min_stock)`
  y unicidad `(product_id, branch_id)`.
- Las columnas `products.stock` / `products.min_stock` se conservan solo como
  espejo legado; el sistema siempre lee/escribe `inventory` usando la sucursal
  de la instalación. Las bases existentes se migran solas al primer arranque:
  el stock histórico pasa a `inventory` de la sucursal local.
- El repositorio de productos recibe la `branch_id` de la instalación y filtra
  por ella en cada lectura/escritura.

**Identidad de la instalación** (tabla local `sys_config`, clave/valor):

| clave              | significado                                            |
| ------------------ | ------------------------------------------------------ |
| `id_sucursal`      | sucursal a la que pertenece el terminal (inventario)   |
| `id_terminal`      | identifica la caja dentro de la sucursal (se autogenera)|
| `supabase_url`     | endpoint del proyecto Supabase                          |
| `supabase_anon_key`| anon key publicable del proyecto                        |

Se configura al momento de la instalación:

```bash
python -m app.main --id-sucursal 0d1f...c92a --id-terminal CAJA-1 \
    --supabase-url https://xyz.supabase.co --supabase-anon-key eyJ...
```

**Cómo filtra el worker de sincronización** (Fase 4, `QThread`):

- `id_sucursal` → al bajar, el worker descarga **solo** el inventario de su
  sucursal (`inventory WHERE branch_id = id_sucursal`) y el catálogo global
  completo; nunca las existencias de otras sucursales.
- `id_terminal` → cada caja trabaja offline; al subir envía sus ventas y al
  bajar recibe las ventas de sus **terminales hermanos** de la misma sucursal
  para mantener historial y stock consistentes (última escritura gana).
- Sin `supabase_url`, el terminal opera 100% offline (modo heredado local).
- En Supabase, el esquema separa `branches` / `terminals` / `products`
  (catálogo) / `inventory` (por sucursal), con RLS activa: lectura del
  catálogo vía anon y el inventario expuesto solo por sucursal;

## Sincronización entre dos cajas (Fase 1, v1)

La app puede operar con **dos cajas de la misma sucursal** que comparten
bodega: cada una vende con su base local SQLite y un botón **“Sincronizar
ahora”** (pestaña Configuración) — y una sincronización automática al arrancar —
intercambia catálogo, inventario y ventas contra el hub Supabase.

- **Qué se sincroniza (v1):** categorías, productos (catálogo), inventario de
  la sucursal y ventas (incluyendo anulaciones y devoluciones). Al bajar una
  venta de la otra caja, se inserta localmente y se replica su efecto de stock
  en `stock_movements` (el cardex la refleja). Pedidos de proveedor, apartados
  y ajustes manuales quedan por ahora locales (v2).
- **Folios de ticket por caja:** cada caja numera sus propios recibos con su
  número de caja (`R-1-000001` en CAJA 1, `R-2-000001` en CAJA 2), así nunca
  se duplican aunque ambas venden sin conexión.
- **Última escritura gana:** si dos cajas editan el mismo producto o el mismo
  stock en distinto momento, gana el cambio con la fecha más reciente.

### Configuración inicial de cada caja

Pestaña **Configuración** → tarjeta *Sucursal y nube*:

| Campo                | CAJA 1        | CAJA 2        |
| -------------------- | ------------- | ------------- |
| ID sucursal          | (mismo en ambas) | (mismo en ambas) |
| Nº de caja           | `1`           | `2`           |
| Supabase URL         | la del proyecto | la del proyecto |
| Supabase anon key    | la del proyecto | la del proyecto |

Guarde y reinicie. La primera sincronización completa el catálogo e inventario
de la segunda caja. También puede configurarse al primer arranque:

```bash
python -m app.main --id-sucursal SUC-XXXX --terminal-num 2 \
    --supabase-url https://xyz.supabase.co --supabase-anon-key eyJ...
```

> Nota: las dos cajas comparten la MISMA bodega. Si además tiene una sucursal
> distinta, cree otro `id_sucursal` y cada terminal bajará sólo su inventario.