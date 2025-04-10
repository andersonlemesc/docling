import easyocr
import io
import base64
import re
from PIL import Image
import numpy as np
import logging
import os
import uuid
import traceback

# Configuração do logger
logger = logging.getLogger(__name__)

class EnhancedOCR:
    """Classe para processamento OCR aprimorado usando EasyOCR separadamente em cada imagem"""
    
    def __init__(self, languages=['pt']):
        """
        Inicializa o leitor EasyOCR com os idiomas especificados
        
        Args:
            languages (list): Lista de idiomas para o EasyOCR (ex: ['pt', 'en'])
        """
        self.languages = languages
        self._reader = None
        
    @property
    def reader(self):
        """Inicializa o leitor EasyOCR sob demanda (lazy loading)"""
        if self._reader is None:
            logger.info(f"Inicializando EasyOCR com idiomas: {self.languages}")
            self._reader = easyocr.Reader(self.languages, gpu=False)
        return self._reader
    
    def process_image(self, image_data, min_confidence=0.5):
        """
        Processa uma imagem com EasyOCR
        
        Args:
            image_data: Dados da imagem (bytes ou numpy array)
            min_confidence: Confiança mínima para resultados OCR
            
        Returns:
            str: Texto extraído da imagem
        """
        try:
            # Converter para formato adequado para EasyOCR
            if isinstance(image_data, bytes):
                img = Image.open(io.BytesIO(image_data))
                img_array = np.array(img)
            else:
                img_array = image_data
                
            # Realizar OCR
            results = self.reader.readtext(img_array)
            
            # Filtrar resultados por confiança e extrair texto
            extracted_text = []
            for (bbox, text, prob) in results:
                if prob >= min_confidence:
                    extracted_text.append(text)
            
            # Retornar texto concatenado ou mensagem se não houver texto
            if extracted_text:
                return " ".join(extracted_text)
            else:
                return "[Nenhum texto detectado na imagem]"
                
        except Exception as e:
            logger.error(f"Erro ao processar imagem com OCR: {str(e)}")
            return f"[Erro no OCR: {str(e)}]"

    def process_base64_image(self, base64_string, min_confidence=0.5):
        """
        Processa uma imagem codificada em base64
        
        Args:
            base64_string: String da imagem em base64
            min_confidence: Confiança mínima para resultados OCR
            
        Returns:
            str: Texto extraído da imagem
        """
        try:
            # Extrair apenas os dados base64 (remover metadados como 'data:image/png;base64,')
            if ',' in base64_string:
                base64_data = base64_string.split(',', 1)[1]
            else:
                base64_data = base64_string
                
            # Decodificar base64
            image_data = base64.b64decode(base64_data)
            
            # Processar a imagem
            return self.process_image(image_data, min_confidence)
            
        except Exception as e:
            logger.error(f"Erro ao processar imagem base64 com OCR: {str(e)}")
            return f"[Erro no OCR para imagem base64: {str(e)}]"

def collect_images_and_ocr_results(markdown_content, ocr_processor, min_confidence=0.5):
    """
    Coleta todas as imagens do markdown e aplica OCR em cada uma
    
    Args:
        markdown_content: Conteúdo markdown com imagens base64
        ocr_processor: Instância de EnhancedOCR
        min_confidence: Confiança mínima para OCR
        
    Returns:
        list: Lista de resultados OCR para cada imagem
    """
    # Identificar imagens em base64 no markdown
    img_pattern = r'!\[.*?\]\((data:image\/[^)]+)\)'
    matches = list(re.finditer(img_pattern, markdown_content))
    
    # Processar cada imagem encontrada
    ocr_results = []
    
    # Log do número de imagens encontradas
    logger.info(f"Coletando OCR para {len(matches)} imagens encontradas no documento")
    
    for i, match in enumerate(matches):
        img_base64 = match.group(1)
        
        # Processar a imagem com OCR
        logger.info(f"Processando imagem {i+1} de {len(matches)}")
        ocr_text = ocr_processor.process_base64_image(img_base64, min_confidence)
        
        # Log do resultado do OCR
        if ocr_text == "[Nenhum texto detectado na imagem]":
            logger.info(f"Imagem {i+1}: Nenhum texto detectado")
        else:
            # Truncar texto muito longo para log
            log_text = ocr_text[:50] + "..." if len(ocr_text) > 50 else ocr_text
            logger.info(f"Imagem {i+1}: Texto detectado - '{log_text}'")
        
        # Sempre adicionar o resultado, independentemente de ter encontrado texto ou não
        ocr_results.append(ocr_text)
    
    return ocr_results

def apply_enhanced_ocr(conv_result, temp_dir, ocr_language="pt", min_confidence=0.5, include_image_data=False):
    """
    Aplica OCR aprimorado ao resultado da conversão e retorna markdown melhorado
    
    Args:
        conv_result: Resultado da conversão do documento
        temp_dir: Diretório temporário
        ocr_language: Idioma para OCR
        min_confidence: Confiança mínima para OCR
        include_image_data: Se True, mantém as imagens em base64 no documento
        
    Returns:
        str: Conteúdo markdown com transcrições OCR
    """
    try:
        logger.info("Iniciando processamento de OCR aprimorado para markdown")
        
        # Criar instância do processador OCR
        ocr_processor = EnhancedOCR(languages=[ocr_language])
        
        # Para processamento OCR, sempre precisamos das imagens primeiro
        logger.info("Salvando documento com imagens para processamento OCR")
        temp_md_file_with_images = os.path.join(temp_dir, f"temp_ocr_with_images_{uuid.uuid4()}.md")
        conv_result.document.save_as_markdown(
            temp_md_file_with_images,
            image_mode="embedded"
        )
        
        # Ler o conteúdo do arquivo com imagens
        with open(temp_md_file_with_images, 'r', encoding='utf-8') as f:
            md_content_with_images = f.read()
        
        # Coletar todas as imagens e processar com OCR
        ocr_results = collect_images_and_ocr_results(
            md_content_with_images, 
            ocr_processor, 
            min_confidence
        )
        
        # Criar um arquivo com o modo de imagem requisitado pelo usuário
        output_image_mode = "embedded" if include_image_data else "placeholder"
        logger.info(f"Gerando markdown final com modo de imagem: {output_image_mode}")
        final_md_file = os.path.join(temp_dir, f"final_md_{uuid.uuid4()}.md")
        
        conv_result.document.save_as_markdown(
            final_md_file,
            image_mode=output_image_mode
        )
        
        # Ler o conteúdo do arquivo final
        with open(final_md_file, 'r', encoding='utf-8') as f:
            final_md_content = f.read()
        
        # Número de resultados OCR coletados
        num_ocr_results = len(ocr_results)
        logger.info(f"Coletados {num_ocr_results} resultados OCR")
        
        # Criar placeholders únicos para cada imagem
        unique_placeholders = []
        for i in range(num_ocr_results):
            unique_placeholders.append(f"<!-- ocr-image-{i+1} -->")
        
        logger.info(f"Criados {len(unique_placeholders)} placeholders únicos")
        
        # Substituir os placeholders no markdown final ou marcar posições das imagens
        if output_image_mode == "placeholder":
            # Quando usamos placeholders, identificamos o padrão padrão de placeholder
            placeholder_pattern = "<!-- image -->"
            
            # Contar quantos placeholders existem no documento
            placeholder_count = final_md_content.count(placeholder_pattern)
            logger.info(f"Encontrados {placeholder_count} placeholders padrão no markdown")
            
            # Verificar se temos placeholders suficientes
            if placeholder_count < num_ocr_results:
                logger.warning(f"Atenção: há mais resultados OCR ({num_ocr_results}) do que placeholders ({placeholder_count})")
            
            # Substituir cada placeholder padrão por um placeholder único
            modified_md = final_md_content
            for i in range(min(placeholder_count, num_ocr_results)):
                modified_md = modified_md.replace(placeholder_pattern, unique_placeholders[i], 1)
                
        else:
            # Para imagens em base64, precisamos localizar cada ocorrência
            img_pattern = r'!\[.*?\]\(data:image\/[^)]+\)'
            img_matches = list(re.finditer(img_pattern, final_md_content))
            
            logger.info(f"Encontradas {len(img_matches)} imagens em base64 no markdown final")
            
            # Verificar se temos imagens suficientes
            if len(img_matches) < num_ocr_results:
                logger.warning(f"Atenção: há mais resultados OCR ({num_ocr_results}) do que imagens ({len(img_matches)})")
            
            # Substituir cada imagem pelo formato: imagem + placeholder único
            modified_md = final_md_content
            for i, match in enumerate(img_matches):
                if i < num_ocr_results:
                    img_tag = match.group(0)
                    replacement = f"{img_tag}\n\n{unique_placeholders[i]}"
                    modified_md = modified_md.replace(img_tag, replacement, 1)
        
        # Agora, adicionar as transcrições OCR nos placeholders únicos
        logger.info("Adicionando transcrições OCR aos placeholders")
        final_md = modified_md
        for i, ocr_text in enumerate(ocr_results):
            if i < len(unique_placeholders):
                placeholder = unique_placeholders[i]
                # Sempre adicionar um bloco OCR, mesmo que não tenha texto detectado
                ocr_block = f"\n\n<!-- OCR Transcription -->\n> **Texto da imagem:** {ocr_text}\n\n"
                final_md = final_md.replace(placeholder, ocr_block)
                
                # Verificar se a substituição funcionou
                if placeholder in final_md:
                    logger.warning(f"Falha ao substituir o placeholder {i+1}: '{placeholder}'")
                else:
                    logger.info(f"Placeholder {i+1} substituído com sucesso")
        
        # Verificação final para garantir que todos os placeholders foram substituídos
        remaining_placeholders = re.findall(r'<!-- ocr-image-\d+ -->', final_md)
        if remaining_placeholders:
            logger.warning(f"Encontrados {len(remaining_placeholders)} placeholders não substituídos. Aplicando correção...")
            
            for placeholder in remaining_placeholders:
                logger.warning(f"Substituindo placeholder restante: {placeholder}")
                ocr_block = f"\n\n<!-- OCR Transcription -->\n> **Texto da imagem:** [Imagem sem texto detectado]\n\n"
                final_md = final_md.replace(placeholder, ocr_block)
        
        logger.info("Processamento OCR aprimorado concluído com sucesso")
        return final_md
        
    except Exception as e:
        logger.error(f"Erro ao aplicar OCR aprimorado: {e}")
        logger.error(traceback.format_exc())
        # Fallback: retornar markdown original sem OCR
        return conv_result.document.export_to_markdown()

def process_document_with_ocr(conv_result, temp_dir, ocr_language="pt", min_confidence=0.5, include_image_data=True):
    """
    Processa o documento com OCR aprimorado, aplicando transcrições às imagens 
    mas sem retornar um formato específico - apenas processa o documento
    
    Args:
        conv_result: Resultado da conversão do documento
        temp_dir: Diretório temporário
        ocr_language: Idioma para OCR
        min_confidence: Confiança mínima para OCR
        include_image_data: Se True, mantém as imagens em base64 
        
    Returns:
        None: Apenas processa o documento sem retornar um formato específico
    """
    try:
        logger.info("Processando documento com OCR aprimorado (sem retornar formato específico)")
        
        # Criar instância do processador OCR
        ocr_processor = EnhancedOCR(languages=[ocr_language])
        
        # Para processamento OCR, sempre precisamos das imagens
        logger.info("Extraindo imagens para processamento OCR")
        temp_md_file_with_images = os.path.join(temp_dir, f"temp_ocr_with_images_{uuid.uuid4()}.md")
        conv_result.document.save_as_markdown(
            temp_md_file_with_images,
            image_mode="embedded"
        )
        
        # Ler o conteúdo do arquivo com imagens
        with open(temp_md_file_with_images, 'r', encoding='utf-8') as f:
            md_content_with_images = f.read()
        
        # Coletar resultados OCR para todas as imagens
        ocr_results = collect_images_and_ocr_results(
            md_content_with_images, 
            ocr_processor, 
            min_confidence
        )
        
        logger.info(f"Coletados {len(ocr_results)} resultados OCR")
        
        # Apenas log dos resultados, sem manipular o documento
        for i, result in enumerate(ocr_results):
            if result and result != "[Nenhum texto detectado na imagem]":
                short_text = result[:30] + "..." if len(result) > 30 else result
                logger.info(f"Imagem {i+1}: '{short_text}'")
            else:
                logger.info(f"Imagem {i+1}: Sem texto detectado")
        
        # Não retornamos nenhum formato específico
        return None
        
    except Exception as e:
        logger.error(f"Erro ao processar documento com OCR aprimorado: {e}")
        logger.error(traceback.format_exc())
        return None