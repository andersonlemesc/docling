from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    PictureDescriptionApiOptions,
    PictureDescriptionVlmOptions,
    EasyOcrOptions,
    OcrMacOptions,
    RapidOcrOptions,
    TesseractCliOcrOptions,
    TesseractOcrOptions
)
from docling.document_converter import PdfFormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling_core.types.doc import PictureItem, ImageRefMode
import uuid
import os
import tempfile
from typing import Optional, Dict, List, Any, Literal
import logging
import shutil
import time
import inspect
import base64
import re
import traceback

# Importação para o OCR aprimorado
from app.enhanced_ocr import apply_enhanced_ocr, process_document_with_ocr

# Configurar FastAPI com timeout aumentado
app = FastAPI()

# Configurar timeout para o FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import asyncio

class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            # Aumentar o timeout para 3600 segundos (1 hora)
            logger.info(f"Processando requisição com timeout de 3600 segundos (1 hora): {request.url}")
            start_time = time.time()
            response = await asyncio.wait_for(call_next(request), timeout=3600)
            process_time = time.time() - start_time
            logger.info(f"Requisição processada em {process_time:.2f} segundos: {request.url}")
            return response
        except asyncio.TimeoutError:
            logger.error(f"Timeout excedido (3600s) ao processar requisição: {request.url}")
            return JSONResponse(
                status_code=504,
                content={"detail": "O processamento da requisição excedeu o limite de 1 hora (3600 segundos)"}
            )
        except Exception as exc:
            logger.error(f"Erro ao processar requisição: {request.url} - {str(exc)}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Erro interno do servidor: {str(exc)}"}
            )

app.add_middleware(TimeoutMiddleware)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Adicione esta função para detecção e tratamento de quebras de página


def detect_and_enhance_page_breaks(html_content):
    """
    Detecta e melhora as quebras de página no HTML exportado para preservar o layout original.
    """
    logger.info("Aplicando tratamento avançado de quebras de página no HTML")

    # 1. Verificar se o HTML já tem uma estrutura de páginas definida
    page_structure_exists = (
        "<div class=\"page\"" in html_content or
        "<div class=\"docpage\"" in html_content or
        "<section class=\"page\"" in html_content
    )

    if page_structure_exists:
        logger.info("Estrutura de páginas existente detectada")

        # Se existir, melhorar a separação visual entre as páginas
        # Adicionar classes CSS para melhorar a visualização de cada página
        if "<div class=\"page\"" in html_content:
            html_content = html_content.replace(
                "<div class=\"page\"",
                "<div class=\"page enhanced-page\""
            )
    else:
        logger.info(
            "Estrutura de páginas não detectada - tentando identificar quebras naturais")

        # 2. Tentar identificar quebras naturais de página
        # Verificar se existem elementos que possam indicar quebras de página
        page_break_indicators = [
            # Títulos geralmente indicam início de nova seção/página
            r'<h1[^>]*>',
            # Elementos de cabeçalho possivelmente indicam novas páginas
            r'<header[^>]*>',
            # Algumas implementações usam comentários para marcar quebras
            r'<!-- page break -->',
            r'<!-- pagebreak -->',
            # Divs específicas para quebra ou novas seções
            r'<div[^>]*class="(section|chapter)[^"]*"[^>]*>',
            # Quebras explícitas
            r'<hr[^>]*class="[^"]*pagebreak[^"]*"[^>]*>',
            # Quebras de seção
            r'<div[^>]*class="[^"]*break[^"]*"[^>]*>'
        ]

        # Temos o conteúdo body?
        body_pattern = re.compile(r'<body[^>]*>(.*?)</body>', re.DOTALL)
        body_match = body_pattern.search(html_content)

        if body_match:
            body_content = body_match.group(1)

            # Criar um sinalizador para rastrear onde inserimos quebras de página
            break_positions = []

            # Encontrar possíveis quebras de página baseadas nos indicadores
            for pattern in page_break_indicators:
                for match in re.finditer(pattern, body_content):
                    # Adicionar a posição de cada potencial quebra de página
                    position = match.start()
                    break_positions.append(position)

            # Ordenar as posições para processá-las na ordem correta
            break_positions.sort()

            # Remover posições muito próximas (evitar quebras duplicadas)
            filtered_positions = []
            last_pos = -1
            min_distance = 100  # Distância mínima entre quebras (caracteres)

            for pos in break_positions:
                if last_pos == -1 or pos - last_pos > min_distance:
                    filtered_positions.append(pos)
                    last_pos = pos

            # Inserir os divisores de página nas posições identificadas
            if filtered_positions:
                logger.info(
                    f"Encontradas {len(filtered_positions)} potenciais quebras de página naturais")

                # Criar o separador de página com estilos
                page_divider = '''
                <div class="enhanced-page-break">
                    <hr class="page-divider">
                </div>
                '''

                # Inserir os divisores no conteúdo
                offset = 0
                new_body = body_content

                for pos in filtered_positions:
                    # Ajustar a posição com o offset acumulado
                    adjusted_pos = pos + offset
                    # Inserir o divisor na posição ajustada
                    new_body = new_body[:adjusted_pos] + \
                        page_divider + new_body[adjusted_pos:]
                    # Atualizar o offset para as próximas inserções
                    offset += len(page_divider)

                # Substituir o conteúdo original do body pelo novo conteúdo com quebras
                html_content = html_content.replace(
                    body_match.group(1), new_body)
            else:
                logger.info("Não foram encontradas quebras de página naturais")

                # 3. Se não encontrarmos quebras naturais, tentar dividir por parágrafos longos
                # ou por mudanças significativas de estilo

                # Dividir o conteúdo em parágrafos
                paragraphs = re.split(
                    r'(<p[^>]*>.*?</p>)', body_content, flags=re.DOTALL)

                if len(paragraphs) > 5:  # Se tivermos uma quantidade razoável de parágrafos
                    logger.info(
                        f"Tentando criar quebras artificiais a cada {len(paragraphs)//3} parágrafos")

                    # Definir um intervalo para inserir quebras (ex: a cada 1/3 do total de parágrafos)
                    page_interval = max(3, len(paragraphs) // 3)

                    # Juntar os parágrafos com quebras nos intervalos definidos
                    new_body = ""
                    for i, para in enumerate(paragraphs):
                        new_body += para
                        if i > 0 and i % page_interval == 0 and para.strip():
                            new_body += page_divider

                    # Substituir o conteúdo original
                    html_content = html_content.replace(
                        body_match.group(1), new_body)

    # 4. Adicionar estilos específicos para quebras de página e páginas melhoradas
    enhanced_page_styles = '''
    <style>
    /* Estilos para quebras de página aprimoradas */
    .enhanced-page-break {
        margin: 30px 0;
        page-break-before: always;
        clear: both;
    }
    
    .page-divider {
        border: none;
        border-top: 1px dashed #999;
        margin: 30px 0;
        height: 1px;
    }
    
    /* Separação visual para páginas */
    .enhanced-page, .page {
        position: relative;
        border: 1px solid #ddd;
        margin-bottom: 40px;
        padding: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        background-color: #fff;
        page-break-after: always;
    }
    
    /* Cabeçalhos e rodapés de página */
    .header, header {
        position: relative;
        border-bottom: 1px solid #eee;
        margin-bottom: 15px;
        padding-bottom: 10px;
    }
    
    .footer, footer {
        position: relative;
        border-top: 1px solid #eee;
        margin-top: 15px;
        padding-top: 10px;
    }
    
    /* Preservar quebras de linha e espaçamento dentro das páginas */
    .enhanced-page p, .page p, .content p, p {
        white-space: pre-wrap;
        margin-bottom: 0.5em;
    }
    
    /* Estilo para impressão */
    @media print {
        .enhanced-page-break {
            display: block;
            page-break-before: always;
        }
        
        .enhanced-page, .page {
            page-break-after: always;
            border: none;
            box-shadow: none;
            margin: 0;
            padding: 0;
        }
    }
    </style>
    '''

    # Adicionar os estilos ao cabeçalho
    if "<head>" in html_content:
        html_content = html_content.replace(
            "<head>", f"<head>{enhanced_page_styles}")
    elif "</title>" in html_content:
        html_content = html_content.replace(
            "</title>", f"</title>{enhanced_page_styles}")
    else:
        html_content = f"{enhanced_page_styles}{html_content}"

    return html_content


@app.post("/process")
async def process_file(
    file: UploadFile = File(...),
    ocr: bool = Query(False, description="Ativa OCR para leitura de imagens"),
    enhanced_ocr: bool = Query(
        False, description="Ativa OCR aprimorado que processa cada imagem individualmente"),
    export_format: str = Query(
        "markdown", description="Formato de exportação: markdown, html ou dict"),
    force_full_page_ocr: bool = Query(
        False, description="Aplica OCR em páginas inteiras, ideal para documentos escaneados"),
    generate_images: bool = Query(
        False, description="Gera imagens em base64 para as páginas e figuras"),
    ocr_language: str = Query(
        "pt", description="Idioma para OCR (ex: pt, en, fr, de)"),
    ocr_min_confidence: float = Query(
        0.5, description="Confiança mínima para OCR (entre 0 e 1)"),
    describe_images: bool = Query(
        False, description="Adiciona descrições às imagens usando LLM"),
    image_description_prompt: str = Query(
        "Descreva esta imagem em detalhes", description="Prompt para descrição de imagens"),
    include_image_data: bool = Query(
        False, description="Inclui imagens como base64 no markdown (se false, apenas placeholders)"),
    include_descriptions: bool = Query(
        True, description="Inclui descrições de imagens no markdown quando disponíveis"),
    preserve_layout: bool = Query(
        True, description="Preserva o layout original, incluindo cabeçalhos, rodapés e quebras de linha"),
    preserve_line_breaks: bool = Query(
        True, description="Preserva todas as quebras de linha do documento original"),
    # Parâmetros para escolha do provedor de LLM
    llm_provider: str = Query(
        "ollama", description="Provedor LLM a ser usado: 'ollama', 'openai' ou 'interno'"),
    # Parâmetros para Ollama
    ollama_url: str = Query("http://localhost:11434/v1/chat/completions",
                            description="URL da API Ollama para descrição de imagens"),
    ollama_model: str = Query(
        "llama3.2-vision:latest", description="Modelo Ollama a ser usado para descrição de imagens"),
    # Parâmetros para OpenAI
    openai_api_key: Optional[str] = Query(
        None, description="Chave da API OpenAI para descrição de imagens"),
    openai_model: str = Query(
        "gpt-4o-mini", description="Modelo OpenAI a ser usado para descrição de imagens"),
    openai_max_tokens: int = Query(
        500, description="Número máximo de tokens para resposta da OpenAI")
):
    temp_dir = None
    file_path = None

    try:
        export_format = export_format.strip().lower()
        logger.info(f"Formato de exportação após limpeza: '{export_format}'")

        # Ler conteúdo do arquivo
        file_content = await file.read()

        # Criar diretório temporário
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file.filename)

        # Salvar arquivo
        with open(file_path, "wb") as f:
            f.write(file_content)

        logger.info(f"Arquivo '{file.filename}' salvo para processamento")

        # Verificar se devemos usar OCR aprimorado ou OCR padrão
        use_enhanced_ocr = enhanced_ocr
        use_standard_ocr = ocr and not enhanced_ocr

        # Configurar opções de pipeline para PDF
        pipeline_options = PdfPipelineOptions()

        # Configurar opções de pipeline para PDF
        pipeline_options = PdfPipelineOptions()

        # Forçar extração de todas as imagens, inclusive pequenas
        try:
            # Configurações para extração de imagens
            if hasattr(pipeline_options, "extract_embedded_images"):
                pipeline_options.extract_embedded_images = True
                logger.info("Configurado extract_embedded_images = True")

            if hasattr(pipeline_options, "min_embedded_image_area"):
                pipeline_options.min_embedded_image_area = 0  # Sem tamanho mínimo
                logger.info("Configurado min_embedded_image_area = 0")

            if hasattr(pipeline_options, "extract_inline_images"):
                pipeline_options.extract_inline_images = True
                logger.info("Configurado extract_inline_images = True")

            # Opções adicionais para garantir extração de todas as imagens
            if hasattr(pipeline_options, "ignore_small_images"):
                pipeline_options.ignore_small_images = False
                logger.info("Configurado ignore_small_images = False")

            if hasattr(pipeline_options, "min_image_size"):
                pipeline_options.min_image_size = 1  # Tamanho mínimo de 1 pixel
                logger.info("Configurado min_image_size = 1")

            # Configurar para incluir imagens de todas as origens
            if hasattr(pipeline_options, "image_sources"):
                pipeline_options.image_sources = [
                    "embedded", "inline", "background", "all"]
                logger.info(
                    "Configurado para extrair imagens de todas as origens")

        except Exception as e:
            logger.warning(
                f"Erro ao configurar opções de extração de imagens: {e}")

        # Tentar configurar a preservação de layout usando diferentes abordagens
        try:
            # Tentar configurar preserve_layout diretamente
            pipeline_options.preserve_layout = True

            # Aplicar configuração de quebras de linha se solicitada
            if preserve_line_breaks:
                if hasattr(pipeline_options, "preserve_line_breaks"):
                    pipeline_options.preserve_line_breaks = True
                    logger.info(
                        "Configurado preserve_line_breaks = True (suporte nativo)")
                else:
                    # Tentar em outras propriedades potenciais
                    for prop_name in ["line_breaks_preservation", "keep_line_breaks", "maintain_line_breaks"]:
                        if hasattr(pipeline_options, prop_name):
                            setattr(pipeline_options, prop_name, True)
                            logger.info(
                                f"Configurado {prop_name} = True para preservação de quebras de linha")
                            break
                    logger.info(
                        "Configurando preservação avançada de quebras de linha")

        except (AttributeError, ValueError) as e:
            logger.warning(
                f"Não foi possível configurar preserve_layout diretamente: {e}")

            # Tentar métodos alternativos para configurar a preservação de layout
            try:
                # Em algumas versões, pode ser uma configuração dentro de um objeto de opções
                if hasattr(pipeline_options, "layout_options") and hasattr(pipeline_options.layout_options, "preserve"):
                    pipeline_options.layout_options.preserve = True
                    logger.info("Configurado layout_options.preserve = True")

                # Algumas versões podem ter um método para habilitar a preservação de layout
                if hasattr(pipeline_options, "enable_layout_preservation"):
                    pipeline_options.enable_layout_preservation()
                    logger.info("Método enable_layout_preservation() chamado")

                # Tente encontrar onde a configuração de layout está armazenada inspecionando os membros
                for attr_name in dir(pipeline_options):
                    if "layout" in attr_name.lower() and not attr_name.startswith("_"):
                        try:
                            layout_attr = getattr(pipeline_options, attr_name)
                            if isinstance(layout_attr, bool):
                                setattr(pipeline_options, attr_name, True)
                                logger.info(f"Configurado {attr_name} = True")
                        except Exception:
                            pass
            except Exception as e2:
                logger.warning(
                    f"Todas as tentativas de configurar a preservação de layout falharam: {e2}")

        # Tentar configurar outras opções de preservação de layout de modo seguro
        try:
            if hasattr(pipeline_options, "keep_headers_footers"):
                pipeline_options.keep_headers_footers = True
                logger.info("Configurado keep_headers_footers = True")

            if hasattr(pipeline_options, "headers_footers_enabled"):
                pipeline_options.headers_footers_enabled = True
                logger.info("Configurado headers_footers_enabled = True")

            # Alguns backends podem ter uma configuração específica para preservação de layout
            if hasattr(pipeline_options, "layout_preservation"):
                pipeline_options.layout_preservation = True
                logger.info("Configurado layout_preservation = True")

            # Configurar para preservar quebras de linha originais
            if hasattr(pipeline_options, "preserve_line_breaks"):
                pipeline_options.preserve_line_breaks = True
                logger.info("Configurado preserve_line_breaks = True")

            # Preservar espaçamento original entre elementos
            if hasattr(pipeline_options, "preserve_spacing"):
                pipeline_options.preserve_spacing = True
                logger.info("Configurado preserve_spacing = True")

            # Algumas implementações podem ter uma configuração para blocos de texto
            if hasattr(pipeline_options, "maintain_text_blocks"):
                pipeline_options.maintain_text_blocks = True
                logger.info("Configurado maintain_text_blocks = True")

            # Verificar se existe alguma opção relacionada a estrutura
            if hasattr(pipeline_options, "maintain_structure"):
                pipeline_options.maintain_structure = True
                logger.info("Configurado maintain_structure = True")

            # Algumas implementações podem usar o termo "original"
            if hasattr(pipeline_options, "keep_original_layout"):
                pipeline_options.keep_original_layout = True
                logger.info("Configurado keep_original_layout = True")

            logger.info("Configurações de preservação de layout processadas")
        except Exception as e:
            logger.warning(
                f"Erro ao configurar opções específicas de preservação de layout: {e}")

            # Configurar OCR padrão (método original) se solicitado
            if use_standard_ocr or use_enhanced_ocr:
                logger.info(f"Ativando OCR com idioma: {ocr_language}")
                # Definir idioma do OCR
                pipeline_options.do_ocr = True

                try:
                    # Importar opções de OCR necessárias
                    from docling.datamodel.pipeline_options import (
                        EasyOcrOptions,
                        TesseractOcrOptions,
                        TesseractCliOcrOptions,
                        OcrMacOptions,
                        RapidOcrOptions
                    )

                    # Criar opções de OCR com force_full_page_ocr ativado
                    try:
                        # Tentar usar EasyOCR primeiro (melhor para vários idiomas)
                        ocr_options = EasyOcrOptions(
                            force_full_page_ocr=True,
                            lang=[ocr_language],
                            min_confidence=ocr_min_confidence
                        )
                        logger.info(
                            "Configurado EasyOcrOptions com force_full_page_ocr=True")
                    except Exception as e:
                        logger.warning(
                            f"Erro ao configurar EasyOcrOptions: {e}, tentando TesseractOcrOptions")

                        # Fallback para TesseractOcrOptions
                        try:
                            ocr_options = TesseractOcrOptions(
                                force_full_page_ocr=True,
                                lang=[ocr_language],
                                min_confidence=ocr_min_confidence
                            )
                            logger.info(
                                "Configurado TesseractOcrOptions com force_full_page_ocr=True")
                        except Exception as e:
                            logger.warning(
                                f"Erro ao configurar TesseractOcrOptions: {e}, tentando TesseractCliOcrOptions")

                            # Último recurso: TesseractCliOcrOptions
                            ocr_options = TesseractCliOcrOptions(
                                force_full_page_ocr=True,
                                lang=[ocr_language]
                            )
                            logger.info(
                                "Configurado TesseractCliOcrOptions com force_full_page_ocr=True")

                    # Atribuir as opções OCR configuradas ao pipeline
                    pipeline_options.ocr_options = ocr_options

                except ImportError as e:
                    logger.warning(f"Erro ao importar classes de OCR: {e}")
                    logger.warning("Continuando com configurações OCR padrão")

                    # Configuração alternativa se não for possível importar as classes
                    try:
                        # Abordagem 1: definir via ocr_options.lang
                        if hasattr(pipeline_options, "ocr_options"):
                            if hasattr(pipeline_options.ocr_options, "lang"):
                                pipeline_options.ocr_options.lang = [
                                    ocr_language]
                            if hasattr(pipeline_options.ocr_options, "force_full_page_ocr"):
                                pipeline_options.ocr_options.force_full_page_ocr = True
                            if hasattr(pipeline_options.ocr_options, "min_confidence"):
                                pipeline_options.ocr_options.min_confidence = ocr_min_confidence
                            if hasattr(pipeline_options.ocr_options, "use_easyocr"):
                                pipeline_options.ocr_options.use_easyocr = True

                        # Abordagem 2: definir via ocr_languages (algumas versões)
                        if hasattr(pipeline_options, "ocr_languages"):
                            pipeline_options.ocr_languages = [ocr_language]
                    except AttributeError as e:
                        logger.warning(
                            f"Erro ao configurar opções específicas de OCR: {e}")
            else:
                # Desativar OCR padrão
                pipeline_options.do_ocr = False

        # Configuração para geração de imagens
        # Sempre geramos imagens se OCR aprimorado estiver ativado
        if generate_images or use_enhanced_ocr:
            logger.info("Ativando geração de imagens")

            # Configurações básicas
            pipeline_options.generate_page_images = True
            pipeline_options.generate_picture_images = True
            pipeline_options.generate_table_images = True

            # Configurações para melhorar a qualidade das imagens
            pipeline_options.images_scale = 1.5  # Escala maior para melhor qualidade
            pipeline_options.generate_parsed_pages = True

            # Configurações para garantir que as imagens sejam incorporadas
            try:
                if hasattr(pipeline_options, "with_embedded_pictures"):
                    pipeline_options.with_embedded_pictures = True

                if hasattr(pipeline_options, "embed_images"):
                    pipeline_options.embed_images = True
            except AttributeError as e:
                logger.warning(
                    f"Erro ao configurar incorporação de imagens: {e}")

        # Configurar descrição de imagens usando LLM se solicitado
        if describe_images:
            logger.info(
                f"Configurando descrição de imagens com provedor: {llm_provider}")

            # Ativar a descrição de imagens
            pipeline_options.do_picture_description = True

            try:
                # Escolher o provedor de LLM para descrição de imagens
                if llm_provider.lower() == "ollama":
                    # Configurar usando API Ollama
                    pipeline_options.picture_description_options = PictureDescriptionApiOptions(
                        url=ollama_url,
                        params={
                            "model": ollama_model,
                            "max_completion_tokens": 500
                        },
                        prompt=image_description_prompt,
                        timeout=90
                    )
                    pipeline_options.enable_remote_services = True
                    logger.info(f"Usando API Ollama para descrição de imagens")

                elif llm_provider.lower() == "openai":
                    # Verificar se a chave da API foi fornecida
                    if not openai_api_key:
                        raise ValueError(
                            "API key da OpenAI é necessária quando usando o provedor 'openai'")

                    # Configurar usando API OpenAI
                    pipeline_options.picture_description_options = PictureDescriptionApiOptions(
                        url="https://api.openai.com/v1/chat/completions",
                        params={
                            "model": openai_model,
                            "max_tokens": openai_max_tokens
                        },
                        prompt=image_description_prompt,
                        timeout=120,
                        headers={
                            "Authorization": f"Bearer {openai_api_key}",
                            "Content-Type": "application/json"
                        }
                    )
                    pipeline_options.enable_remote_services = True
                    logger.info(f"Usando API OpenAI para descrição de imagens")

                else:
                    # Usar o modelo interno do Docling (fallback)
                    pipeline_options.picture_description_options = PictureDescriptionVlmOptions(
                        prompt=image_description_prompt,
                        generation_config={
                            "max_new_tokens": 200, "do_sample": False}
                    )
                    logger.info(
                        "Usando modelo VLM interno do Docling para descrição de imagens")
            except Exception as e:
                logger.warning(f"Erro ao configurar provedor LLM: {e}")
                # Usar modelo interno como fallback em caso de erro
                pipeline_options.picture_description_options = PictureDescriptionVlmOptions(
                    prompt=image_description_prompt,
                    generation_config={
                        "max_new_tokens": 200, "do_sample": False}
                )
                logger.warning(
                    "Usando modelo interno como fallback devido a erro na configuração")

        # Criar conversor com as opções configuradas
        doc_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options,
                    backend=PyPdfiumDocumentBackend
                )
            }
        )

        # Converter o documento
        logger.info(f"Iniciando conversão do arquivo")
        start_time = time.time()
        conv_result = doc_converter.convert(file_path)
        end_time = time.time() - start_time
        logger.info(f"Documento convertido em {end_time:.2f} segundos")

        # Verificar se o método iterate_items existe
        has_iterate_items = hasattr(conv_result.document, 'iterate_items')

        # IMPORTANTE: Verificar o formato solicitado e aplicar OCR aprimorado se necessário
        # Verificar se devemos aplicar OCR aprimorado após conversão
        if use_enhanced_ocr:
            logger.info(f"Aplicando OCR aprimorado com idioma: {ocr_language}")
            try:
                # Para formato markdown, aplicamos OCR e retornamos o resultado como markdown
                if export_format.lower() == "markdown":
                    md_content = apply_enhanced_ocr(
                        conv_result,
                        temp_dir,
                        ocr_language=ocr_language,
                        min_confidence=ocr_min_confidence,
                        include_image_data=include_image_data
                    )
                    logger.info(
                        "OCR aprimorado aplicado com sucesso para formato markdown")
                    return {"markdown": md_content}
                else:
                    # Para outros formatos, processamos o documento com OCR mas não retornamos ainda
                    process_document_with_ocr(
                        conv_result,
                        temp_dir,
                        ocr_language=ocr_language,
                        min_confidence=ocr_min_confidence,
                        include_image_data=True
                    )
                    logger.info(
                        f"OCR aprimorado aplicado, continuando para exportação no formato: {export_format}")
                    # Não retornamos aqui - continuamos o fluxo para exportar no formato solicitado
            except Exception as e:
                logger.error(f"Erro ao aplicar OCR aprimorado: {e}")
                logger.error(traceback.format_exc())
                logger.warning(
                    "Continuando com o fluxo normal de processamento após falha no OCR aprimorado")

        # Exportar no formato solicitado conforme a requisição
        # Importante: para markdown só continuamos se não houver OCR aprimorado
        if export_format.lower() == "markdown" and not use_enhanced_ocr:
            md_content = None

            # Abordagem com placeholders únicos para cada imagem
            try:
                # Primeiro, vamos coletar as descrições das imagens
                all_descriptions = []

                if has_iterate_items:
                    for element, *_ in conv_result.document.iterate_items():
                        if isinstance(element, PictureItem):
                            if hasattr(element, 'annotations') and element.annotations:
                                desc = "\n".join(
                                    [ann.text for ann in element.annotations])
                                all_descriptions.append(desc)
                            else:
                                # Verificar outros atributos para descrição
                                for attr in ['description', 'caption', 'text', 'ocr_result']:
                                    if hasattr(element, attr) and getattr(element, attr):
                                        all_descriptions.append(
                                            getattr(element, attr))
                                        break
                                else:
                                    all_descriptions.append(
                                        "Sem descrição disponível")

                # Número total de descrições coletadas
                num_descriptions = len(all_descriptions)
                logger.info(
                    f"Coletadas {num_descriptions} descrições de imagens")

                # Criar placeholders únicos para cada imagem
                unique_placeholders = []
                for i in range(num_descriptions):
                    unique_placeholders.append(f"<!-- image-{i+1} -->")

                # Criar um arquivo temporário para o markdown
                md_file_with_unique_placeholders = os.path.join(
                    temp_dir, f"with_unique_placeholders.md")

                # Definir o modo de imagem
                image_mode_value = "embedded" if include_image_data else "placeholder"

                # Primeiro, salvar o markdown normalmente
                temp_md_file = os.path.join(temp_dir, "temp_md.md")
                conv_result.document.save_as_markdown(
                    temp_md_file,
                    image_mode=image_mode_value
                )

                # Ler o conteúdo do arquivo
                with open(temp_md_file, 'r', encoding='utf-8') as f:
                    md_content = f.read()

                # Substituir os placeholders padrão por placeholders únicos
                if not include_image_data:
                    # Padrão de placeholder para substituição
                    placeholder_pattern = "<!-- image -->"

                    # Contar quantos placeholders existem no documento
                    placeholder_count = md_content.count(placeholder_pattern)
                    logger.info(
                        f"Encontrados {placeholder_count} placeholders no markdown")

                    # Substituir cada placeholder padrão por um placeholder único
                    for i in range(min(placeholder_count, num_descriptions)):
                        md_content = md_content.replace(
                            placeholder_pattern, unique_placeholders[i], 1)
                else:
                    # Para imagens em base64, precisamos localizar cada ocorrência
                    img_pattern = r'!\[.*?\]\(data:image\/[^)]+\)'
                    img_matches = list(re.finditer(img_pattern, md_content))

                    # Substituir cada imagem pelo formato:
                    # imagem + placeholder único
                    for i, match in enumerate(img_matches):
                        if i < num_descriptions:
                            img_tag = match.group(0)
                            replacement = f"{img_tag}\n\n{unique_placeholders[i]}"
                            md_content = md_content.replace(
                                img_tag, replacement, 1)

                # Agora, adicionar as descrições nos placeholders únicos
                if include_descriptions and all_descriptions:
                    for i, placeholder in enumerate(unique_placeholders):
                        if i < len(all_descriptions):
                            desc_text = f"\n\n**Descrição da imagem:** {all_descriptions[i]}\n\n"
                            md_content = md_content.replace(
                                placeholder, desc_text)

                # Escrever o markdown modificado em um arquivo
                with open(md_file_with_unique_placeholders, 'w', encoding='utf-8') as f:
                    f.write(md_content)

                logger.info(f"Markdown gerado com placeholders únicos")

            except Exception as e:
                logger.warning(
                    f"Erro ao processar markdown com placeholders únicos: {e}")
                # Fallback para o método padrão
                try:
                    # Definir o modo de imagem
                    image_mode_value = "embedded" if include_image_data else "placeholder"
                    logger.info(
                        f"Usando image_mode='{image_mode_value}' para markdown (fallback)")

                    # Criar arquivo temporário para o markdown
                    md_file = os.path.join(temp_dir, f"{uuid.uuid4()}.md")

                    # Salvar markdown com o modo apropriado
                    conv_result.document.save_as_markdown(
                        md_file,
                        image_mode=image_mode_value
                    )

                    # Ler o arquivo salvo
                    with open(md_file, 'r', encoding='utf-8') as f:
                        md_content = f.read()

                except Exception as inner_e:
                    logger.warning(
                        f"Erro ao criar markdown (fallback): {inner_e}")
                    md_content = conv_result.document.export_to_markdown()

            # Se ainda não temos conteúdo markdown, usar export_to_markdown como último recurso
            if md_content is None:
                logger.warning("Usando export_to_markdown como último recurso")
                md_content = conv_result.document.export_to_markdown()

            return {"markdown": md_content}

        # Modificações para a parte do HTML no método process_file
        elif export_format.lower() == "html":
            # Para HTML, tentar exportação com parâmetros para incluir imagens e preservar layout
            try:
                # Definir opções para exportação HTML com segurança
                # Usaremos um dicionário para armazenar opções válidas
                export_options = {
                    "image_mode": "embedded"  # Essa opção é comum e provavelmente suportada
                }

                # Verificar se outras opções são suportadas verificando a assinatura do método
                if hasattr(conv_result.document, "export_to_html_with_options"):
                    # Inspecionar os parâmetros aceitos pelo método
                    import inspect
                    try:
                        signature = inspect.signature(
                            conv_result.document.export_to_html_with_options)
                        param_names = list(signature.parameters.keys())

                        # Adicionar opções apenas se forem suportadas
                        if "embedding_images" in param_names:
                            export_options["embedding_images"] = True

                        if "preserve_layout" in param_names:
                            export_options["preserve_layout"] = True
                            logger.info(
                                "Adicionado preserve_layout=True às opções de exportação HTML")

                        if "keep_headers_footers" in param_names:
                            export_options["keep_headers_footers"] = True
                            logger.info(
                                "Adicionado keep_headers_footers=True às opções de exportação HTML")

                        logger.info(
                            f"Opções de exportação HTML detectadas: {export_options}")

                        # Chamar o método com as opções suportadas
                        html_content = conv_result.document.export_to_html_with_options(
                            **export_options)

                    except (TypeError, ValueError) as e:
                        logger.warning(f"Erro ao inspecionar parâmetros: {e}")
                        # Tentar com opções padrão mínimas
                        html_content = conv_result.document.export_to_html_with_options(
                            embedding_images=True,
                            image_mode="embedded"
                        )

                # Alternativa: usar save_as_html se disponível
                elif hasattr(conv_result.document, "save_as_html"):
                    # Caminho temporário para o HTML
                    html_file = os.path.join(temp_dir, f"{uuid.uuid4()}.html")

                    # Verificar quais parâmetros são aceitos
                    try:
                        import inspect
                        signature = inspect.signature(
                            conv_result.document.save_as_html)
                        param_names = list(signature.parameters.keys())

                        save_options = {}

                        # Adicionar opções apenas se forem suportadas
                        if "image_mode" in param_names:
                            save_options["image_mode"] = "embedded"

                        if "preserve_layout" in param_names:
                            save_options["preserve_layout"] = True

                        if "keep_headers_footers" in param_names:
                            save_options["keep_headers_footers"] = True

                        logger.info(
                            f"Opções de save_as_html detectadas: {save_options}")

                        # Chamar o método com as opções suportadas
                        conv_result.document.save_as_html(
                            html_file, **save_options)

                    except (TypeError, ValueError) as e:
                        logger.warning(
                            f"Erro ao inspecionar parâmetros de save_as_html: {e}")
                        # Tentar com opções mínimas
                        conv_result.document.save_as_html(
                            html_file, image_mode="embedded")

                    # Ler arquivo HTML salvo
                    with open(html_file, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                else:
                    # Método padrão de exportação se não encontrar métodos específicos
                    html_content = conv_result.document.export_to_html()

                # Verificar se o HTML tem imagens, e tentar recuperá-las se não tiver
                if "<img" not in html_content and generate_images:
                    logger.warning(
                        "HTML não contém tags de imagem. Tentando recuperar imagens...")

                    # restante do código existente para recuperar imagens...

                # Pré-processamento e limpeza do HTML para corrigir problemas de formatação
                # Esta função irá processar o HTML para garantir que as quebras de linha
                # sejam aplicadas apenas no conteúdo de texto, não entre as tags HTML
                # Função aprimorada para limpar e estruturar o HTML, preservando quebras de linha
                def clean_html_structure(html_content, preserve_layout=True, preserve_line_breaks=True):
                    """
                    Processa o HTML para garantir que quebras de linha e espaçamento sejam preservados,
                    mantendo a estrutura original do documento.
                    """
                    logger.info(
                        "Iniciando limpeza e estruturação avançada do HTML")

                    # Primeiro, normalizar quebras de linha no HTML para \n
                    html_content = html_content.replace(
                        '\r\n', '\n').replace('\r', '\n')

                    # Verificar se existem quebras de linha literais (\n) no HTML
                    if '\\n' in html_content:
                        logger.info(
                            "Detectado '\\n' literais no HTML - convertendo para quebras de linha reais")
                        html_content = html_content.replace('\\n', '\n')

                    # Pré-processamento: identificar e preservar blocos que já estão bem formatados
                    # Por exemplo, elementos como <pre>, <code>, ou <div class="preserve">
                    preserve_blocks = []
                    preserve_pattern = re.compile(
                        r'(<pre[^>]*>.*?</pre>|<code[^>]*>.*?</code>|<div[^>]*class="preserve"[^>]*>.*?</div>)', re.DOTALL)

                    def replace_preserve_blocks(match):
                        block = match.group(1)
                        placeholder = f"___PRESERVE_BLOCK_{len(preserve_blocks)}___"
                        preserve_blocks.append(block)
                        return placeholder

                    # Substituir blocos preservados por placeholders
                    html_content = preserve_pattern.sub(
                        replace_preserve_blocks, html_content)

                    # Separar as tags HTML do conteúdo de texto
                    parts = re.split(r'(<[^>]+>)', html_content)
                    clean_parts = []
                    in_paragraph = False
                    in_header = False
                    in_special_block = False
                    consecutive_newlines = 0

                    for part in parts:
                        # Se for uma tag HTML
                        if part.startswith('<') and part.endswith('>'):
                            # Verificar se estamos entrando/saindo de blocos especiais
                            if re.match(r'<(p|h[1-6]|div)[^>]*>', part):
                                in_special_block = True
                                if re.match(r'<p[^>]*>', part):
                                    in_paragraph = True
                                if re.match(r'<h[1-6][^>]*>', part):
                                    in_header = True
                            elif re.match(r'</(p|h[1-6]|div)>', part):
                                in_special_block = False
                                in_paragraph = False
                                in_header = False
                                # Adicionar quebra após parágrafos e cabeçalhos
                                if preserve_line_breaks and not clean_parts[-1].endswith('<br>'):
                                    clean_parts.append('<br>')
                                    consecutive_newlines = 1

                            # Preservar <br> tags originais
                            if part.lower() == '<br>' or part.lower() == '<br/>':
                                if consecutive_newlines < 2:  # Evitar mais que duas quebras consecutivas
                                    clean_parts.append(part)
                                    consecutive_newlines += 1
                                continue

                            # Não adicionar <br> nas tags, apenas limpar espaços extras
                            clean_part = part.strip()
                            clean_parts.append(clean_part)
                            consecutive_newlines = 0
                        else:
                            # Para conteúdo de texto
                            if preserve_line_breaks and '\n' in part:
                                # Detectar diferentes padrões de quebra de linha
                                lines = part.split('\n')
                                processed_lines = []

                                for i, line in enumerate(lines):
                                    line = line.rstrip()  # Manter espaços à esquerda para preservar indentação

                                    # Preservar espaços significativos convertendo para &nbsp;
                                    if line.startswith('  '):
                                        # Contar espaços iniciais
                                        lead_spaces = len(
                                            line) - len(line.lstrip(' '))
                                        line = '&nbsp;' * lead_spaces + \
                                            line[lead_spaces:]

                                    if line.strip() or i == 0 or i == len(lines) - 1:
                                        processed_lines.append(line)
                                    else:
                                        # Linhas vazias no meio - convertê-las em <br>
                                        # Será convertida em <br> abaixo
                                        processed_lines.append('')

                                # Juntar as linhas com <br> apenas se não estiver no início/fim de blocos
                                result = ""
                                for i, line in enumerate(processed_lines):
                                    if i > 0:
                                        if line.strip() or in_paragraph or in_special_block:
                                            result += '<br>'
                                            consecutive_newlines = 1

                                    result += line

                                clean_parts.append(result)

                            else:
                                # Caso não tenha quebras ou não esteja preservando layout
                                if part.strip():
                                    # Preservar espaçamento significativo
                                    if preserve_layout and re.search(r' {2,}', part):
                                        # Preservar identação transformando em &nbsp;
                                        clean_part = re.sub(
                                            r' {2,}', lambda m: '&nbsp;' * len(m.group(0)), part)
                                        clean_parts.append(clean_part)
                                    else:
                                        clean_parts.append(part)
                                # Texto vazio mas não nulo (apenas espaços)
                                elif part:
                                    clean_parts.append(part)

                    # Unir todos os pedaços processados
                    cleaned_html = ''.join(clean_parts)

                    # Corrigir problemas comuns
                    # Remover <br> adjacentes a tags de bloco
                    cleaned_html = re.sub(
                        r'<br>\s*<(p|div|h[1-6])[^>]*>', r'<\1>', cleaned_html)
                    cleaned_html = re.sub(
                        r'</(p|div|h[1-6])>\s*<br>', r'</\1>', cleaned_html)

                    # Corrigir quebras duplas ou triplas
                    cleaned_html = re.sub(
                        r'<br>\s*<br>\s*<br>', '<br><br>', cleaned_html)

                    # Corrigir spans vazios
                    cleaned_html = re.sub(
                        r'<span[^>]*>\s*</span>', '', cleaned_html)

                    # Restaurar os blocos preservados
                    for i, block in enumerate(preserve_blocks):
                        cleaned_html = cleaned_html.replace(
                            f"___PRESERVE_BLOCK_{i}___", block)

                    # Tratar cabeçalhos e torná-los visualmente mais distintos
                    if preserve_layout:
                        for h_level in range(1, 7):
                            h_pattern = re.compile(
                                f'<h{h_level}([^>]*)>(.*?)</h{h_level}>', re.DOTALL)

                            def enhance_header(match):
                                attributes = match.group(1)
                                content = match.group(2)

                                # Adicionar classe para destacar visualmente
                                if 'class="' in attributes:
                                    attributes = attributes.replace(
                                        'class="', 'class="enhanced-header ')
                                else:
                                    attributes += ' class="enhanced-header"'

                                # Adicionar margem e linha decorativa
                                return f'<h{h_level}{attributes}>{content}</h{h_level}><div class="header-separator"></div>'

                            cleaned_html = h_pattern.sub(
                                enhance_header, cleaned_html)

                    return cleaned_html

                # Processar o HTML para corrigir problemas de formatação
                if preserve_layout or preserve_line_breaks:
                    logger.info(
                        "Aplicando limpeza e formatação avançada do HTML")
                    html_content = clean_html_structure(
                        html_content, preserve_layout, preserve_line_breaks)

                # Aplicar tratamento para quebras de página
                html_content = detect_and_enhance_page_breaks(html_content)

                # Verificar se o HTML gerado contém imagens
                has_images = "<img" in html_content
                logger.info(
                    f"HTML gerado com {len(html_content)} caracteres. Contém imagens: {has_images}")

                # Verificar se o conteúdo HTML tem figcaption para depuração
                has_figcaption = "<figcaption" in html_content
                logger.info(f"HTML contém figcaption: {has_figcaption}")

                # Verificar se há cabeçalhos e rodapés preservados
                has_header = "<div class=\"header\"" in html_content or "<header" in html_content
                has_footer = "<div class=\"footer\"" in html_content or "<footer" in html_content
                logger.info(
                    f"HTML contém cabeçalho: {has_header}, rodapé: {has_footer}")

                # Importante: sempre retornar o conteúdo HTML
                return {"html": html_content}

            except Exception as e:
                logger.warning(f"Erro ao exportar HTML personalizado: {e}")
                # Adicione isso para ver o erro completo
                logger.warning(traceback.format_exc())
                # Fallback: exportar sem parâmetros
                html_content = conv_result.document.export_to_html()
                return {"html": html_content}

        elif export_format.lower() in ["dict", "json"]:
            # Para dict/json, usar o método padrão
            try:
                dict_content = conv_result.document.export_to_dict()
            except Exception as e:
                logger.warning(f"Erro ao exportar para dict: {e}")
                # Fallback: se falhar, tentar criar um dict básico
                dict_content = {
                    "error": "Não foi possível exportar para dict/json"}

            # Adicionar informações adicionais para debug
            if isinstance(dict_content, dict):
                dict_content["_debug"] = {
                    "processingTime": f"{end_time:.2f} segundos"
                }

                # Se estamos descrevendo imagens, adicione informações sobre descrições
                if describe_images and has_iterate_items:
                    image_desc_count = 0
                    for element, *_ in conv_result.document.iterate_items():
                        if isinstance(element, PictureItem) and hasattr(element, 'annotations') and element.annotations:
                            image_desc_count += 1

                    dict_content["_debug"]["imagesWithDescriptions"] = image_desc_count
                    dict_content["_debug"]["descriptionProvider"] = llm_provider

                # Adicionar informações sobre OCR aprimorado
                if use_enhanced_ocr:
                    dict_content["_debug"]["enhancedOCR"] = {
                        "enabled": True,
                        "language": ocr_language,
                        "minConfidence": ocr_min_confidence
                    }

            return dict_content

        else:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"Formato de exportação '{export_format}' não suportado."}
            )

    except Exception as e:
        logger.error(f"Erro ao processar documento: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": f"Erro ao processar o arquivo: {str(e)}"}
        )

    finally:
        # Limpar arquivos temporários de forma segura
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Arquivos temporários removidos")
        except Exception as e:
            logger.warning(f"Erro ao limpar diretório temporário: {str(e)}")
