# API Docling

API para processamento de documentos usando a biblioteca Docling.

## Funcionalidades

- Processamento de diversos formatos de documentos (PDF, DOCX, HTML, imagens, etc.)
- Suporte a OCR para documentos digitalizados com configuração de idioma
- OCR aprimorado com processamento individualizado de imagens
- Configuração de confiança mínima para OCR
- Extração de imagens em base64 (funciona em todos os formatos de exportação)
- Descrição automática de imagens usando modelos LLM (Ollama)
- Exportação em diferentes formatos (Markdown, HTML, JSON)

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/andersonlemescdocling-api.git
cd docling-api

# Instalar dependências
pip install -r requirements.txt

# Iniciar a API
uvicorn app.main:app --reload
```

## Uso da API

A API expõe um endpoint POST `/process` que aceita arquivos PDF e várias opções de processamento.

### Endpoint

```
POST /process
```

### Parâmetros

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| file | Arquivo | - | Arquivo PDF a ser processado (obrigatório) |
| export_format | string | "markdown" | Formato de exportação: "markdown", "html" ou "dict" |
| ocr | boolean | false | Ativa OCR para leitura de texto em imagens |
| enhanced_ocr | boolean | false | Ativa OCR aprimorado que processa cada imagem individualmente |
| ocr_language | string | "pt" | Idioma para OCR (ex: "pt", "en", "fr", "de") |
| ocr_min_confidence | float | 0.5 | Confiança mínima para OCR (entre 0 e 1) |
| force_full_page_ocr | boolean | false | Aplica OCR em páginas inteiras, ideal para documentos escaneados |
| generate_images | boolean | false | Gera imagens em base64 para as páginas e figuras |
| include_image_data | boolean | false | Inclui imagens como base64 no markdown (se false, apenas placeholders) |
| include_descriptions | boolean | true | Inclui descrições de imagens no markdown quando disponíveis |
| describe_images | boolean | false | Adiciona descrições às imagens usando LLM |
| image_description_prompt | string | "Descreva esta imagem em detalhes" | Prompt para descrição de imagens |
| llm_provider | string | "ollama" | Provedor LLM a ser usado: "ollama", "openai" ou "interno" |

### Parâmetros específicos para OpenAI

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| openai_api_key | string | null | Chave da API OpenAI para descrição de imagens |
| openai_model | string | "gpt-4o-mini" | Modelo OpenAI a ser usado para descrição de imagens |
| openai_max_tokens | integer | 500 | Número máximo de tokens para resposta da OpenAI |

### Parâmetros específicos para Ollama

| Parâmetro | Tipo | Padrão | Descrição |
|-----------|------|--------|-----------|
| ollama_url | string | "http://localhost:11434/v1/chat/completions" | URL da API Ollama |
| ollama_model | string | "llama3.2-vision:latest" | Modelo Ollama a ser usado para descrição de imagens |

## Exemplos de Uso

### Processamento Básico

```bash
curl -X POST "http://localhost:8000/process" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Com OCR Básico Ativado

```bash
curl -X POST "http://localhost:8000/process?ocr=true&ocr_language=pt" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Com OCR Aprimorado e Configuração de Confiança

```bash
curl -X POST "http://localhost:8000/process?enhanced_ocr=true&ocr_language=pt&ocr_min_confidence=0.7" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Com OCR de Página Completa (para documentos escaneados)

```bash
curl -X POST "http://localhost:8000/process?ocr=true&force_full_page_ocr=true&ocr_language=pt" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Com Descrição de Imagens via OpenAI

```bash
curl -X POST "http://localhost:8000/process?describe_images=true&llm_provider=openai&openai_api_key=sua_chave_api&openai_model=gpt-4o-mini&generate_images=true" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Markdown com Placeholders e Descrições (sem imagens em base64)

```bash
curl -X POST "http://localhost:8000/process?describe_images=true&generate_images=true&include_image_data=false&include_descriptions=true&llm_provider=openai&openai_api_key=sua_chave_api" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Markdown com Imagens em Base64 e Descrições

```bash
curl -X POST "http://localhost:8000/process?describe_images=true&generate_images=true&include_image_data=true&include_descriptions=true&llm_provider=openai&openai_api_key=sua_chave_api" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

### Combinando OCR Aprimorado e Descrição de Imagens

```bash
curl -X POST "http://localhost:8000/process?enhanced_ocr=true&ocr_min_confidence=0.65&describe_images=true&generate_images=true&include_image_data=true&llm_provider=ollama" \
  -H "accept: application/json" \
  -F "file=@documento.pdf"
```

## Resposta

A API retorna um objeto JSON contendo o documento processado no formato solicitado:

```json
{
  "markdown": "# Título do Documento\n\nConteúdo do documento...\n\n<!-- image -->\n\n**Descrição da imagem:** Descrição gerada pelo LLM...\n\n**Texto OCR:** Texto extraído da imagem...\n"
}
```

## Notas

- Para utilizar o OCR, certifique-se de que as dependências do EasyOCR estão instaladas
- O OCR aprimorado (`enhanced_ocr=true`) processa cada imagem individualmente, produzindo resultados mais precisos
- Utilize `ocr_min_confidence` para filtrar resultados de OCR com baixa confiança
- Para documentos escaneados, ative `force_full_page_ocr=true` para processamento completo de páginas
- Para descrição de imagens com OpenAI, é necessário fornecer uma chave de API válida
- Para usar o Ollama, certifique-se de que um servidor Ollama está em execução e acessível na URL especificada
- O processamento de documentos grandes pode levar algum tempo, especialmente com OCR e descrição de imagens ativados

## Limitações

- O tamanho máximo do arquivo é determinado pela configuração do FastAPI
- A qualidade do OCR depende da qualidade das imagens no documento
- A qualidade das descrições de imagens depende do modelo LLM utilizado