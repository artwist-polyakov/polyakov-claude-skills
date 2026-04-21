---
name: genome-analyzer
description: "Parses genetic data from VCF files, identifies relevant SNPs via web research (GWAS Catalog, SNPedia), and generates structured risk and trait reports. Covers inherited traits, substance metabolism (caffeine, alcohol, medication), athletic predispositions, disease risks, and gene-based nutrition. Use when the user asks about their genetics, hereditary traits, predispositions, substance metabolism, athletic abilities, disease risks, or gene-based nutrition from a VCF file. Triggers (RU): генетика, VCF, наследственные признаки, предрасположенности, метаболизм веществ, спортивные способности, риски заболеваний, питание на основе генов. Triggers (EN): genetics, genome, VCF, SNP, genetic analysis, DNA report."
---

# Genome Analyzer

Анализирует генетические данные пользователя из VCF файла: находит релевантные SNP через веб-исследование, сопоставляет с генотипом и генерирует структурированный отчёт.

## VCF файл
VCF файл находится в текущей директории. Имя файла может быть любым (*.vcf).

## Алгоритм работы

### Шаг 0: Найти VCF файл
Используй **Glob** для поиска VCF файла в текущей директории:
- `pattern`: `*.vcf`

Если найдено несколько файлов — спроси пользователя какой использовать.
Если файл не найден — сообщи пользователю.

### Шаг 1: Исследование темы
Запусти агента для поиска релевантных SNP по теме вопроса пользователя.

Используй **Task tool** с параметрами:
- `subagent_type`: "general-purpose"
- `model`: "sonnet"
- `prompt`: Поиск SNP (rsID), связанных с темой вопроса

Агент должен:
- Использовать WebSearch для поиска генов и SNP по теме
- Искать в источниках: GWAS Catalog, SNPedia, научные статьи
- Вернуть список rsID с описанием эффектов каждого аллеля (risk/protective)

### Шаг 2: Поиск в геноме
Используй **Grep** для поиска найденных rsID в VCF файле (путь из Шага 0):
- `pattern`: `rs123456|rs789012|rs...` (объединить через |)
- `path`: путь к VCF файлу из Шага 0
- `output_mode`: "content"

**Валидация:** если ни один rsID не найден в VCF — сообщи пользователю, предложи расширить поиск (другие SNP по теме) или уточнить формат файла.

### Шаг 3: Интерпретация генотипов
Расшифруй генотипы из VCF:
- `0/0` = гомозигота по референсу (REF/REF)
- `0/1` = гетерозигота (REF/ALT)
- `1/1` = гомозигота по альтернативе (ALT/ALT)

Сопоставь генотип с информацией об аллелях из Шага 1.

## Формат ответа

Отчёт должен быть визуально привлекательным и структурированным. Используй шаблон и эмодзи из [references/REPORT_TEMPLATE.md](references/REPORT_TEMPLATE.md).

## Стиль написания

- Пиши простым языком, избегай научного жаргона
- Объясняй что означает каждый генотип НА ПРАКТИКЕ
- Давай конкретные, actionable рекомендации
- Используй **жирный текст** для выделения важного
- Разбивай текст на короткие абзацы
- Добавляй горизонтальные разделители `---` между секциями
- В таблицах используй эмодзи для визуального восприятия
